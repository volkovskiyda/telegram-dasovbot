"""Change feed read/apply, snapshot reconcile and handoff re-stamp (plan item 05).

Real temp-file databases: ATTACH and the backup API need files, and the
paging cutoff must be exercised against the actual union query.
"""
import json
import os
import tempfile
import unittest

from dasovbot.database import (
    init_db, read_changes, apply_changes, write_snapshot, reconcile_from_snapshot,
    keys_above_rev, load_rev, get_meta, last_applied_key, upsert_video, delete_video,
    load_videos, KEYED_TABLES,
)
from dasovbot.models import VideoInfo, Intent, Subscription
from dasovbot.state import BotState
from tests.helpers import make_config


class FeedTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.states = []

    async def asyncTearDown(self):
        for state in self.states:
            await state.close()

    async def make_state(self, name: str) -> BotState:
        db = await init_db(os.path.join(self.tmp.name, name, 'bot.db'))
        state = BotState(db=db, config=make_config())
        await state.migrate_and_load()
        self.states.append(state)
        return state

    async def pull_all(self, source: BotState, target: BotState, since: int = 0, limit: int = 500,
                       peer: str = 'peer') -> set:
        """Pull source's feed into target until has_more is False; return touched keys."""
        touched, pages = set(), 0
        while True:
            page = await read_changes(source.db, since, limit)
            result = await target.apply_remote_page(page, peer)
            touched |= result['touched']
            pages += 1
            since = page['until']
            if not page['has_more']:
                return touched
            self.assertLess(pages, 100, 'feed never ends')

    async def dump(self, state: BotState) -> dict:
        out = {}
        for table, column in KEYED_TABLES.items():
            cursor = await state.db.execute(f"SELECT {column}, data, rev FROM {table} ORDER BY {column}")
            out[table] = [tuple(r) for r in await cursor.fetchall()]
        cursor = await state.db.execute("SELECT tbl, key, rev FROM tombstones ORDER BY tbl, key")
        out['tombstones'] = [tuple(r) for r in await cursor.fetchall()]
        cursor = await state.db.execute(
            "SELECT user_id, url, source, requested_at, rev FROM requests ORDER BY user_id, url, requested_at")
        out['requests'] = [tuple(r) for r in await cursor.fetchall()]
        return out


class TestReadChangesPaging(FeedTestCase):
    async def test_pages_cover_every_row_exactly_once(self):
        src = await self.make_state('src')
        for i in range(1200):
            if i % 3 == 0:
                await src.set_video(f'v{i}', VideoInfo(title=f'T{i}'))
            elif i % 3 == 1:
                await src.set_user(f'u{i}', {'i': i})
            else:
                await src.record_request(str(i), f'url{i}', 'inline')
        seen, since, pages = [], 0, 0
        while True:
            page = await read_changes(src.db, since, 500)
            pages += 1
            for table, rows in page['rows'].items():
                seen.extend(r[-1] for r in rows)
            since = page['until']
            if not page['has_more']:
                break
        self.assertEqual(pages, 3)
        self.assertEqual(sorted(seen), list(range(1, 1201)))

    async def test_rows_sharing_a_rev_never_split(self):
        src = await self.make_state('src')
        # Three tables carrying the same rev 5 (as rows applied from a peer can)
        await src.db.execute("INSERT INTO videos (key, data, rev) VALUES ('a', '{\"title\":\"a\"}', 5)")
        await src.db.execute("INSERT INTO intents (key, data, rev) VALUES ('b', '{}', 5)")
        await src.db.execute("INSERT INTO users (chat_id, data, rev) VALUES ('c', '{}', 5)")
        await src.db.execute("INSERT INTO users (chat_id, data, rev) VALUES ('d', '{}', 6)")
        await src.db.commit()
        page = await read_changes(src.db, 0, 2)
        self.assertEqual(page['until'], 5)
        self.assertTrue(page['has_more'])
        self.assertEqual(len(page['rows']['videos']), 1)
        self.assertEqual(len(page['rows']['intents']), 1)
        self.assertEqual([r[0] for r in page['rows']['users']], ['c'])
        page = await read_changes(src.db, 5, 2)
        self.assertEqual([r[0] for r in page['rows']['users']], ['d'])
        self.assertFalse(page['has_more'])

    async def test_since_at_or_beyond_counter_is_empty(self):
        src = await self.make_state('src')
        await src.set_video('v', VideoInfo(title='T'))
        page = await read_changes(src.db, 1, 10)
        self.assertEqual((page['until'], page['has_more']), (1, False))
        self.assertEqual(sum(len(v) for v in page['rows'].values()), 0)
        page = await read_changes(src.db, 99, 10)
        self.assertEqual((page['since'], page['until'], page['has_more']), (99, 99, False))

    async def test_page_shape_includes_tombstones_and_requests(self):
        src = await self.make_state('src')
        await src.set_intent('q', Intent(chat_ids=['1']))
        await src.pop_intent('q')
        await src.record_request('7', 'u', 'download')
        page = await read_changes(src.db, 0, 10)
        self.assertEqual(page['rows']['intents'], [])
        self.assertEqual(page['tombstones'][0][:3], ['intents', 'q', 2])
        self.assertEqual(page['rows']['requests'][0][:3], ['7', 'u', 'download'])
        self.assertEqual(page['rows']['requests'][0][4], 3)
        self.assertIsInstance(page['rows']['videos'], list)


class TestApplyRemotePage(FeedTestCase):
    async def test_rows_land_in_db_and_memory_without_side_effects(self):
        src = await self.make_state('src')
        dst = await self.make_state('dst')
        await src.set_video('v', VideoInfo(title='T', webpage_url='https://x'))
        await src.set_intent('q', Intent(chat_ids=['1'], priority=3))
        await src.set_user('1', {'name': 'A'})
        await src.set_subscription('s', Subscription(chat_ids=['1'], title='S'))
        await src.ban_user('9', 'Eve')
        await src.record_request('1', 'https://x', 'download')

        touched = await self.pull_all(src, dst)

        self.assertEqual(dst.videos['v'].title, 'T')
        self.assertEqual(dst.intents['q'].priority, 3)
        self.assertEqual(dst.users['1'], {'name': 'A'})
        self.assertEqual(dst.subscriptions['s'].title, 'S')
        self.assertTrue(dst.is_banned(9))
        self.assertEqual(dst.video_requesters, {'https://x': ['1']})
        self.assertEqual(dst.user_requests['1']['count'], 1)
        self.assertEqual(dst.download_queue.qsize(), 0, 'applying must not wake the worker')
        self.assertEqual(dst.intent_retry_after, {})
        self.assertEqual(await self.dump(dst), await self.dump(src))
        self.assertEqual(dst.rev, src.rev)
        self.assertEqual(await load_rev(dst.db), src.rev)
        self.assertEqual(await get_meta(dst.db, last_applied_key('peer')), src.rev)
        self.assertEqual(touched, {('videos', 'v'), ('intents', 'q'), ('users', '1'),
                                   ('subscriptions', 's'), ('banned_users', '9')})

    async def test_tombstone_deletes_row_and_memory(self):
        src = await self.make_state('src')
        dst = await self.make_state('dst')
        await src.set_intent('q', Intent(chat_ids=['1']))
        await src.set_subscription('s', Subscription(chat_ids=['1']))
        await self.pull_all(src, dst)
        dst.intent_retry_after['q'] = 123.0
        await src.pop_intent('q')
        await src.pop_subscription('s')
        await self.pull_all(src, dst, since=dst.rev)
        self.assertNotIn('q', dst.intents)
        self.assertNotIn('q', dst.intent_retry_after)
        self.assertNotIn('s', dst.subscriptions)
        self.assertEqual(await self.dump(dst), await self.dump(src))

    async def test_older_tombstone_is_ignored(self):
        dst = await self.make_state('dst')
        await upsert_video(dst.db, 'v', VideoInfo(title='new'), rev=50)
        dst.videos['v'] = VideoInfo(title='new')
        dst.rev = 50
        page = {'since': 0, 'until': 10, 'has_more': False, 'rows': {},
                'tombstones': [['videos', 'v', 10, '20260101_000000']]}
        result = await dst.apply_remote_page(page, 'peer')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['tombstones'], 0)
        self.assertIn('v', dst.videos)
        self.assertEqual((await load_videos(dst.db))['v'].title, 'new')
        self.assertEqual(dst.rev, 50, 'counter never lowered')

    async def test_recreated_key_clears_tombstone(self):
        src = await self.make_state('src')
        dst = await self.make_state('dst')
        await src.set_video('v', VideoInfo(title='one'))
        await delete_video(src.db, 'v', rev=src.next_rev(), deleted_at='t')
        await self.pull_all(src, dst)
        self.assertEqual((await self.dump(dst))['tombstones'], [('videos', 'v', 2)])
        await src.set_video('v', VideoInfo(title='two'))
        await self.pull_all(src, dst, since=dst.rev)
        self.assertEqual((await self.dump(dst))['tombstones'], [])
        self.assertEqual(dst.videos['v'].title, 'two')

    async def test_requests_dedupe_and_aggregate(self):
        dst = await self.make_state('dst')
        rows = [['1', 'u', 'inline', '20260101_000001', 1], ['1', 'u', 'inline', '20260101_000001', 2],
                ['2', 'u', 'inline', '20260101_000002', 3]]
        page = {'since': 0, 'until': 3, 'has_more': False, 'rows': {'requests': rows}, 'tombstones': []}
        result = await dst.apply_remote_page(page, 'peer')
        self.assertEqual(result['requests'], 2)
        self.assertEqual(dst.video_requesters, {'u': ['1', '2']})
        self.assertEqual(dst.user_requests['1'], {'count': 1, 'last_at': '20260101_000001'})
        # the same page again is a no-op for the aggregates
        result = await dst.apply_remote_page(page, 'peer')
        self.assertEqual(result['requests'], 0)
        self.assertEqual(dst.user_requests['1']['count'], 1)

    async def test_unknown_tombstone_table_is_skipped(self):
        dst = await self.make_state('dst')
        page = {'since': 0, 'until': 1, 'has_more': False, 'rows': {},
                'tombstones': [['sync_meta', 'rev', 1, 't']]}
        result = await dst.apply_remote_page(page, 'peer')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(await load_rev(dst.db), 1)


class TestSnapshotReconcile(FeedTestCase):
    async def test_local_becomes_snapshot_and_requests_union(self):
        src = await self.make_state('src')
        dst = await self.make_state('dst')
        # Shared history
        await src.set_video('shared', VideoInfo(title='same'))
        await src.set_user('1', {'n': 1})
        await src.record_request('1', 'shared', 'download')
        await self.pull_all(src, dst)
        # Diverge: src moves on, dst holds stale rows the feed will never fix
        await src.set_video('src-only', VideoInfo(title='new'))
        await src.set_user('1', {'n': 2})
        await src.pop_subscription('never')  # no tombstone: key never existed
        await src.set_subscription('s', Subscription(title='S'))
        await src.pop_subscription('s')
        await src.record_request('1', 'src-only', 'download')
        await upsert_video(dst.db, 'dst-only', VideoInfo(title='stale'), rev=999)
        await dst.db.execute("INSERT INTO tombstones VALUES ('videos', 'ghost', 998, 't')")
        await dst.db.commit()
        dst.videos['dst-only'] = VideoInfo(title='stale')
        await dst.record_request('2', 'dst-only', 'inline')

        snap = os.path.join(self.tmp.name, 'snap.db')
        await write_snapshot(src.db, snap)
        result = await reconcile_from_snapshot(dst.db, snap, 'peer')
        await dst.reload_from_db()

        src_dump, dst_dump = await self.dump(src), await self.dump(dst)
        for table in [*KEYED_TABLES, 'tombstones']:
            self.assertEqual(dst_dump[table], src_dump[table], table)
        self.assertEqual({r[:2] for r in dst_dump['requests']}, {('1', 'shared'), ('1', 'src-only'), ('2', 'dst-only')})
        self.assertEqual(result['snapshot_rev'], src.rev)
        self.assertEqual(await load_rev(dst.db), max(src.rev, 999))
        self.assertEqual(dst.rev, max(src.rev, 999))
        self.assertEqual(await get_meta(dst.db, last_applied_key('peer')), src.rev)
        self.assertEqual(dst.videos['src-only'].title, 'new')
        self.assertNotIn('dst-only', dst.videos)
        self.assertEqual(dst.users['1'], {'n': 2})
        self.assertEqual(dst.video_requesters['dst-only'], ['2'])
        self.assertEqual(result['changed']['videos'], {'upserted': 1, 'deleted': 1})
        self.assertEqual(result['changed']['tombstones'], {'upserted': 1, 'deleted': 1})
        # the attachment is gone and the connection still works
        cursor = await dst.db.execute("PRAGMA database_list")
        self.assertEqual([r[1] for r in await cursor.fetchall()], ['main'])

    async def test_identical_rows_are_not_rewritten(self):
        src = await self.make_state('src')
        dst = await self.make_state('dst')
        await src.set_video('v', VideoInfo(title='T'))
        await self.pull_all(src, dst)
        snap = os.path.join(self.tmp.name, 'snap.db')
        await write_snapshot(src.db, snap)
        result = await reconcile_from_snapshot(dst.db, snap, 'peer')
        self.assertEqual(result['changed']['videos'], {'upserted': 0, 'deleted': 0})


class TestReconcileAfterReturn(FeedTestCase):
    async def test_standby_wins_on_touched_keys_and_survivors_are_restamped(self):
        primary = await self.make_state('primary')
        standby = await self.make_state('standby')
        # Common history up to rev 100
        for i in range(97):
            await primary.set_user(f'u{i}', {'i': i})
        await primary.set_video('A', VideoInfo(title='A0'))
        await primary.set_video('B', VideoInfo(title='B0'))
        await primary.set_video('C', VideoInfo(title='C0'))
        self.assertEqual(primary.rev, 100)
        await self.pull_all(primary, standby, peer='primary')
        handoff_rev = standby.rev  # standby takes over here (= 100)
        # Primary wrote A and B (revs 101, 102) that the standby never saw
        await primary.set_video('A', VideoInfo(title='A1-primary'))
        await primary.set_video('B', VideoInfo(title='B1-primary'))
        await primary.set_intent('gone', Intent(chat_ids=['1']))
        await primary.pop_intent('gone')  # tombstone 104, also unseen
        # Meanwhile the standby (ACTIVE) touched B and deleted C
        await standby.set_video('B', VideoInfo(title='B1-standby'))
        await delete_video(standby.db, 'C', rev=standby.next_rev(), deleted_at='t')
        standby.videos.pop('C')
        standby_counter = standby.rev

        async def pull(since):
            return await self.pull_all(standby, primary, since=since, peer='standby')

        result = await primary.reconcile_after_return(handoff_rev, pull)

        self.assertEqual(primary.videos['B'].title, 'B1-standby')
        self.assertNotIn('C', primary.videos)
        self.assertEqual(primary.videos['A'].title, 'A1-primary')
        dump = await self.dump(primary)
        revs = {key: rev for key, _data, rev in dump['videos']}
        self.assertGreater(revs['A'], standby_counter, 'survivor re-stamped above the standby counter')
        self.assertEqual(revs['B'], standby_counter - 1, 'standby row keeps the standby rev')
        tomb = {(t, k): r for t, k, r in dump['tombstones']}
        self.assertGreater(tomb[('intents', 'gone')], standby_counter)
        self.assertEqual(tomb[('videos', 'C')], standby_counter)
        self.assertEqual(result['restamped'], 2)
        self.assertEqual(result['survivors'], 3)
        # The standby's next incremental pull from its cursor receives exactly the survivors
        page = await read_changes(primary.db, standby_counter, 100)
        self.assertEqual([r[0] for r in page['rows']['videos']], ['A'])
        self.assertEqual([t[:2] for t in page['tombstones']], [['intents', 'gone']])
        self.assertEqual(await get_meta(primary.db, last_applied_key('standby')), standby_counter)

    async def test_keys_above_rev(self):
        state = await self.make_state('s')
        await state.set_video('v', VideoInfo(title='T'))
        await state.set_user('u', {})
        await state.pop_intent('nothing')
        await state.set_intent('q', Intent())
        await state.pop_intent('q')
        above = await keys_above_rev(state.db, 1)
        self.assertEqual(above['videos'], [])
        self.assertEqual(above['users'], ['u'])
        self.assertEqual(above['tombstones'], [('intents', 'q')])
