"""Sync client against a real peer node served by aiohttp's test server (plan item 08)."""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestServer

from dasovbot.dashboard.server import create_app
from dasovbot.database import delete_video, get_meta, last_applied_key
from dasovbot.models import VideoInfo, Subscription
from dasovbot.services.sync import (
    SyncClient, SyncError, DeveloperNotifier, META_LAST_HEARTBEAT_AT, META_LAST_SYNC_AT, META_LAST_SYNC_REV,
    META_LAST_SNAPSHOT_AT,
)
from dasovbot.state import BotState
from tests.helpers import make_config, make_memory_db
from tests.test_sync_endpoints import make_controller

SECRET = 'shared-secret'


class SyncClientTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._env = patch.dict('os.environ', {'SYNC_SECRET': SECRET})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.peer_state = BotState(db=await make_memory_db(), config=make_config(node_name='other'))
        self.addAsyncCleanup(self.peer_state.db.close)
        self.peer_ctl = make_controller(role='active')
        self.server = TestServer(create_app(self.peer_state, self.peer_ctl))
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.local = BotState(db=await make_memory_db(), config=make_config(node_name='me'))
        self.addAsyncCleanup(self.local.db.close)
        self.t = 0.0
        self.client = self.make_client()
        self.addAsyncCleanup(self.client.aclose)

    def make_client(self, secret=SECRET, peer_url=None) -> SyncClient:
        config = make_config(node_name='me', node_role='standby', sync_secret=secret,
                             peer_url=peer_url or str(self.server.make_url('')).rstrip('/'))
        return SyncClient(config, self.local, clock=lambda: self.t)


class TestHeartbeat(SyncClientTestCase):
    async def test_success_persists_timestamp_and_peer_name(self):
        self.assertEqual(self.client.peer_name, '127.0.0.1')
        reply = await self.client.heartbeat()
        self.assertEqual(reply['role'], 'active')
        self.assertEqual(reply['node'], 'me' if False else self.peer_ctl.status()['node'])
        self.assertEqual(self.client.peer_name, self.peer_ctl.status()['node'])
        self.assertIsNotNone(self.client.last_heartbeat_at)
        self.assertEqual(await get_meta(self.local.db, META_LAST_HEARTBEAT_AT), self.client.last_heartbeat_at)
        self.assertEqual(self.client.sync_status()['last_heartbeat_at'], self.client.last_heartbeat_at)

    async def test_wrong_secret_returns_none(self):
        client = self.make_client(secret='wrong')
        self.addAsyncCleanup(client.aclose)
        self.assertIsNone(await client.heartbeat())
        self.assertIsNone(client.last_heartbeat_at)

    async def test_unreachable_peer_returns_none(self):
        client = self.make_client(peer_url='http://127.0.0.1:1')
        self.addAsyncCleanup(client.aclose)
        self.assertIsNone(await client.heartbeat())

    async def test_load_restores_persisted_values(self):
        await self.client.heartbeat()
        again = self.make_client()
        self.addAsyncCleanup(again.aclose)
        await again.load()
        self.assertEqual(again.last_heartbeat_at, self.client.last_heartbeat_at)


class TestPullChanges(SyncClientTestCase):
    async def test_walks_pages_and_persists_cursor(self):
        await self.local.set_video('seed', VideoInfo(title='no bootstrap'))
        for i in range(1200):
            await self.peer_state.set_user(str(i), {'i': i})
        await self.client.heartbeat()
        with patch('dasovbot.services.sync.MAX_PAGES_PER_TICK', 2):
            result = await self.client.pull_changes()
        self.assertEqual(result['applied'], 1000, 'two pages of 500 per tick')
        self.assertEqual(len(self.local.users), 1000)
        self.assertEqual(self.client.last_sync_rev, 1000)
        result = await self.client.pull_changes()
        self.assertEqual(result['applied'], 200)
        self.assertEqual(len(self.local.users), 1200)
        peer = self.client.peer_name
        self.assertEqual(await get_meta(self.local.db, last_applied_key(peer)), 1200)
        self.assertEqual(await get_meta(self.local.db, META_LAST_SYNC_REV), 1200)
        self.assertEqual(await get_meta(self.local.db, META_LAST_SYNC_AT), self.client.last_sync_at)
        self.assertEqual((await self.client.pull_changes())['applied'], 0)

    async def test_explicit_since_pulls_to_the_end_and_returns_touched(self):
        await self.local.set_video('seed', VideoInfo(title='not empty: no bootstrap'))
        for i in range(1100):
            await self.peer_state.set_video(f'v{i}', VideoInfo(title=str(i)))
        await self.client.heartbeat()
        with patch('dasovbot.services.sync.MAX_PAGES_PER_TICK', 1):
            result = await self.client.pull_changes(since=1000)
        self.assertEqual(len(result['touched']), 100)
        self.assertIn(('videos', 'v1099'), result['touched'])

    async def test_explicit_since_lets_peer_tombstones_delete_higher_local_rows(self):
        # Reconcile pull: the peer deleted 'gone' at a low rev of its own
        # counter while this node, writing on its own, stamped the same key
        # far higher. The peer's view above the handoff point wins
        await self.peer_state.set_video('gone', VideoInfo(title='peer'))
        await self.peer_state.pop_intent('x')                     # no-op, keeps the counter moving
        await delete_video(self.peer_state.db, 'gone', rev=self.peer_state.next_rev(), deleted_at='t')
        await self.local.set_video('gone', VideoInfo(title='local'))
        self.local.rev = 999
        await self.local.set_video('gone', VideoInfo(title='local, stamped 1000'))
        await self.client.heartbeat()
        await self.client.pull_changes(since=0)
        self.assertNotIn('gone', self.local.videos)
        # The regular incremental pull keeps the old rule: a newer local row survives
        await self.local.set_video('kept', VideoInfo(title='local'))
        await self.peer_state.set_video('kept', VideoInfo(title='peer'))
        await delete_video(self.peer_state.db, 'kept', rev=self.peer_state.next_rev(), deleted_at='t')
        await self.client.pull_changes()
        self.assertIn('kept', self.local.videos)

    async def test_bootstrap_snapshot_when_local_is_empty(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.peer_state.set_subscription('s', Subscription(title='S'))
        await self.client.heartbeat()
        with patch.object(self.client, 'pull_snapshot', wraps=self.client.pull_snapshot) as spy:
            await self.client.pull_changes()
        spy.assert_awaited_once()
        self.assertEqual(self.local.videos['v'].title, 'T')
        self.assertEqual(self.local.subscriptions['s'].title, 'S')
        self.assertEqual(self.local.rev, self.peer_state.rev)
        # a second pull is incremental: no snapshot
        with patch.object(self.client, 'pull_snapshot', wraps=self.client.pull_snapshot) as spy:
            await self.client.pull_changes()
        spy.assert_not_awaited()

    async def test_explicit_since_on_an_empty_node_still_bootstraps(self):
        # The controller asks a never-synced node to reconcile from the peer's
        # handoff_rev: it must snapshot first, not take a partial feed
        for i in range(30):
            await self.peer_state.set_video(f'v{i}', VideoInfo(title=str(i)))
        await self.client.heartbeat()
        with patch.object(self.client, 'pull_snapshot', wraps=self.client.pull_snapshot) as spy:
            result = await self.client.pull_changes(since=28)
        spy.assert_awaited_once()
        self.assertEqual(len(self.local.videos), 30)
        self.assertEqual(result['touched'], set(), 'everything came via the snapshot, nothing after it')
        self.assertEqual(self.client.last_sync_rev, 30)

    async def test_upgraded_node_with_data_starts_incrementally(self):
        await self.local.set_video('old', VideoInfo(title='pre-HA'))
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.client.heartbeat()
        with patch.object(self.client, 'pull_snapshot', wraps=self.client.pull_snapshot) as spy:
            await self.client.pull_changes()
        spy.assert_not_awaited()
        self.assertIn('v', self.local.videos)

    async def test_http_error_raises_sync_error(self):
        await self.local.set_video('x', VideoInfo(title='x'))  # no bootstrap
        client = self.make_client(secret='wrong')
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(SyncError) as ctx:
            await client.pull_changes()
        self.assertIn('401', str(ctx.exception))
        self.assertIsNone(client.last_sync_at, 'failure leaves readiness untouched')

    async def test_connection_error_raises_sync_error(self):
        await self.local.set_video('x', VideoInfo(title='x'))
        client = self.make_client(peer_url='http://127.0.0.1:1')
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(SyncError):
            await client.pull_changes()


class TestSnapshot(SyncClientTestCase):
    async def test_snapshot_reconciles_and_removes_temp_file(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.local.set_video('stale', VideoInfo(title='old'))
        tmp = tempfile.mkdtemp()
        known = os.path.join(tmp, 'snap.db')
        fd = os.open(known, os.O_CREAT | os.O_WRONLY)
        await self.client.heartbeat()
        with patch('dasovbot.services.sync.mkstemp', return_value=(fd, known)):
            await self.client.pull_snapshot()
        self.assertFalse(os.path.exists(known))
        self.assertEqual(set(self.local.videos), {'v'})
        self.assertEqual(self.client.last_sync_rev, self.peer_state.rev)
        self.assertEqual(await get_meta(self.local.db, META_LAST_SNAPSHOT_AT), self.client.last_snapshot_at)
        self.assertEqual(self.client.last_sync_at, self.client.last_snapshot_at)

    async def test_failed_download_raises_and_removes_temp_file(self):
        tmp = tempfile.mkdtemp()
        known = os.path.join(tmp, 'snap.db')
        fd = os.open(known, os.O_CREAT | os.O_WRONLY)
        client = self.make_client(secret='wrong')
        self.addAsyncCleanup(client.aclose)
        with patch('dasovbot.services.sync.mkstemp', return_value=(fd, known)):
            with self.assertRaises(SyncError):
                await client.pull_snapshot()
        self.assertFalse(os.path.exists(known))
        self.assertIsNone(client.last_snapshot_at)

    async def assert_rejected_untouched(self, fragment: str):
        await self.local.set_video('stale', VideoInfo(title='old'))
        await self.client.heartbeat()
        with self.assertRaises(SyncError) as ctx:
            await self.client.pull_snapshot()
        self.assertIn('snapshot rejected', str(ctx.exception))
        self.assertIn(fragment, str(ctx.exception))
        self.assertEqual(set(self.local.videos), {'stale'})
        cursor = await self.local.db.execute("SELECT key FROM videos")
        self.assertEqual([r[0] for r in await cursor.fetchall()], ['stale'])
        self.assertIsNone(self.client.last_snapshot_at)

    async def damage_peer_row(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.peer_state.db.execute("UPDATE videos SET data = ? WHERE key = 'v'", ('{"title" "T"}',))
        await self.peer_state.db.commit()

    async def test_snapshot_with_bad_json_row_is_rejected_before_reconcile(self):
        # Oct 2026: a bit flip on the serving host broke one videos row; it was
        # reconciled in and reload_from_db failed afterwards. A peer that
        # predates its own check (or a flip after it) still sends such a file
        await self.damage_peer_row()
        with patch('dasovbot.dashboard.sync.validate_snapshot'):
            await self.assert_rejected_untouched("videos 'v': bad JSON")

    async def test_peer_refuses_its_own_damaged_snapshot_and_alerts_itself(self):
        # Nov 2026: the damaged row sat in the serving node's page cache; the
        # standby downloaded and rejected it every tick for 7 hours
        await self.damage_peer_row()
        await self.local.set_video('stale', VideoInfo(title='old'))
        await self.client.heartbeat()
        with self.assertRaises(SyncError) as ctx:
            await self.client.pull_snapshot()
        self.assertTrue(ctx.exception.peer_alerted)
        self.assertIn("snapshot: HTTP 500: snapshot failed validation on other: videos 'v': bad JSON",
                      str(ctx.exception))
        self.assertEqual(set(self.local.videos), {'stale'})
        self.peer_ctl.notifier.error.assert_awaited_once()
        self.assertEqual(self.peer_ctl.notifier.error.await_args.kwargs, {'key': 'snapshot_serve'})
        self.assertIn('refused to serve its snapshot', self.peer_ctl.notifier.error.await_args.args[0])

    async def test_failed_snapshot_backs_off_5_min_doubling_to_the_hour(self):
        await self.client.heartbeat()
        attempts = []

        async def fail():
            attempts.append(self.t)
            raise SyncError('snapshot rejected: boom')
        with patch.object(self.client, '_download_and_apply_snapshot', side_effect=fail):
            await self.client.maybe_snapshot()          # arms the timer
            self.t += 3600
            with self.assertRaises(SyncError) as ctx:
                await self.client.maybe_snapshot()
            self.assertEqual(str(ctx.exception), 'snapshot rejected: boom; next try in 5 min')
            for wait in (300, 600, 1200, 2400, 3600, 3600):
                self.t += wait - 1
                self.assertFalse(await self.client.maybe_snapshot(), 'no download before the backoff is over')
                self.t += 1
                with self.assertRaises(SyncError):
                    await self.client.maybe_snapshot()
        self.assertEqual(attempts, [3600, 3900, 4500, 5700, 8100, 11700, 15300])
        self.assertEqual(self.client.snapshot_failures, 7)

    async def test_applied_snapshot_clears_the_backoff(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.client.heartbeat()
        with patch.object(self.client, '_download_and_apply_snapshot', side_effect=SyncError('boom')):
            with self.assertRaises(SyncError):
                await self.client.pull_snapshot()
        self.assertEqual(self.client.snapshot_retry_in(), 300)
        self.t += 300
        self.assertTrue(await self.client.maybe_snapshot())
        self.assertEqual((self.client.snapshot_failures, self.client.snapshot_retry_in()), (0, None))
        self.t += 3599
        with patch.object(self.client, 'pull_snapshot', new_callable=AsyncMock) as spy:
            self.assertFalse(await self.client.maybe_snapshot(), 'back on the hourly schedule')
            spy.assert_not_awaited()

    async def test_bootstrap_honours_the_backoff(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.client.heartbeat()
        with patch.object(self.client, '_download_and_apply_snapshot', side_effect=SyncError('boom')) as download:
            with self.assertRaises(SyncError):
                await self.client.pull_changes()
            with self.assertRaises(SyncError) as ctx:
                await self.client.pull_changes()
        self.assertEqual(download.await_count, 1, 'an empty node does not re-download every tick either')
        self.assertEqual(str(ctx.exception), 'bootstrap snapshot failed 1× in a row; next try in 5 min')

    async def test_snapshot_checksum_mismatch_is_rejected(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        with patch('dasovbot.dashboard.sync.file_sha256', return_value='0' * 64):
            await self.assert_rejected_untouched('checksum mismatch')

    async def test_snapshot_without_checksum_header_is_accepted(self):
        # A peer that predates the checksum: the row check alone guards it
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.client.heartbeat()
        with patch('dasovbot.dashboard.sync.file_sha256', return_value=''):
            await self.client.pull_snapshot()
        self.assertEqual(set(self.local.videos), {'v'})

    async def test_maybe_snapshot_arms_then_pulls_hourly(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.client.heartbeat()
        with patch.object(self.client, 'pull_snapshot', new_callable=AsyncMock) as spy:
            await self.client.maybe_snapshot()      # arms the timer only
            self.t += 3599
            await self.client.maybe_snapshot()
            spy.assert_not_awaited()
            self.t += 1
            await self.client.maybe_snapshot()
            spy.assert_awaited_once()


class TestHandoffAndHandback(SyncClientTestCase):
    async def test_request_handoff_true_on_202(self):
        self.assertTrue(await self.client.request_handoff(manual=True))
        self.peer_ctl.handoff_requested_by_peer.assert_awaited_with(manual=True)

    async def test_request_handoff_false_on_409(self):
        from dasovbot.services.ha import HaError
        self.peer_ctl.handoff_requested_by_peer.side_effect = HaError('not active')
        try:
            self.assertFalse(await self.client.request_handoff(manual=False))
        finally:
            self.peer_ctl.handoff_requested_by_peer.side_effect = None

    async def test_request_handoff_false_when_unreachable(self):
        client = self.make_client(peer_url='http://127.0.0.1:1')
        self.addAsyncCleanup(client.aclose)
        self.assertFalse(await client.request_handoff(manual=True))

    async def test_on_handed_back_keeps_cursor_and_marks_fresh(self):
        await self.client.heartbeat()
        key = last_applied_key(self.client.peer_name)
        from dasovbot.database import set_meta
        await set_meta(self.local.db, key, 120)
        await self.local.db.commit()
        # Wrote plenty while ACTIVE: the counter is far past the cursor
        self.local.rev = 500
        await self.client.on_handed_back()
        # The cursor must not jump to the counter: the peer's re-stamped
        # survivors carry revs between the two and would be skipped
        self.assertEqual(await get_meta(self.local.db, key), 120)
        self.assertEqual(self.client.last_sync_rev, 120)
        self.assertIsNotNone(self.client.last_sync_at)
        self.assertEqual(await get_meta(self.local.db, META_LAST_SYNC_AT), self.client.last_sync_at)

    async def test_aclose_closes_session(self):
        await self.client.heartbeat()
        session = self.client._session
        self.assertFalse(session.closed)
        await self.client.aclose()
        self.assertTrue(session.closed)
        self.assertIsNotNone(await self.client.heartbeat(), 'a new session is opened on demand')


class TestDeveloperNotifier(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.t = 0.0
        self.notifier = DeveloperNotifier(make_config(developer_chat_id='42'), clock=lambda: self.t)
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()

    async def test_without_bot_only_logs(self):
        with self.assertLogs('dasovbot.services.sync', level='INFO') as cm:
            await self.notifier.transition('🟢 me is now ACTIVE')
        self.assertIn('ACTIVE', '\n'.join(cm.output))

    async def test_transitions_always_sent(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.transition('one')
        await self.notifier.transition('two')
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.bot.send_message.assert_awaited_with(chat_id='42', text='two')

    async def test_notify_is_never_collapsed(self):
        # The admin API's audit lines: two bans in a row are two messages
        self.notifier.attach_bot(self.bot)
        with self.assertLogs('dasovbot.services.sync', level='INFO') as cm:
            await self.notifier.notify('API: banned 1')
            await self.notifier.notify('API: banned 1')
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.bot.send_message.assert_awaited_with(chat_id='42', text='API: banned 1')
        self.assertEqual(sum('API: banned 1' in line for line in cm.output), 2)

    async def test_identical_transition_collapsed_within_window(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.transition('flap')
        self.t += 60
        await self.notifier.transition('flap')
        self.t += 60
        await self.notifier.transition('other')
        self.assertEqual([c.kwargs['text'] for c in self.bot.send_message.await_args_list], ['flap', 'other'])

    async def test_collapsed_count_reported_once_the_window_passes(self):
        self.notifier.attach_bot(self.bot)
        for _ in range(4):
            await self.notifier.transition('flap')
            self.t += 120
        self.t += 600
        await self.notifier.transition('flap')
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.bot.send_message.assert_awaited_with(chat_id='42', text='flap (+3 collapsed in the last 18 min)')
        await self.notifier.transition('flap')       # a fresh window: collapsed again, count restarts
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_events_recorded_in_state_history(self):
        from tests.helpers import make_state
        state = make_state()
        notifier = DeveloperNotifier(make_config(developer_chat_id='42'), clock=lambda: self.t, state=state)
        notifier.attach_bot(self.bot)
        await notifier.transition('🟢 me is now ACTIVE')
        await notifier.transition('🟢 me is now ACTIVE')     # collapsed for the chat, still history
        await notifier.error('⚠️ sync error')
        await notifier.error('⚠️ sync error again')           # rate-limited: not sent, not recorded
        self.assertEqual([(e['kind'], e['text']) for e in state.ha_events],
                         [('transition', '🟢 me is now ACTIVE'), ('transition', '🟢 me is now ACTIVE'),
                          ('error', '⚠️ sync error')])
        self.assertTrue(all(e['at'] for e in state.ha_events))
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_quiet_transition_recorded_not_sent(self):
        from tests.helpers import make_state
        state = make_state()
        notifier = DeveloperNotifier(make_config(developer_chat_id='42'), clock=lambda: self.t, state=state)
        notifier.attach_bot(self.bot)
        await notifier.transition('🟡 me drained', quiet=True)
        self.assertEqual([(e['kind'], e['text']) for e in state.ha_events], [('transition', '🟡 me drained')])
        self.bot.send_message.assert_not_awaited()

    def sent(self):
        return [(c.kwargs['text'], c.kwargs.get('disable_notification', False))
                for c in self.bot.send_message.await_args_list]

    async def test_error_episode_first_sent_then_one_silent_reminder_per_hour(self):
        # Nov 2026: one rejected snapshot every 20 s became a message every 10 min for 7 hours
        self.notifier.attach_bot(self.bot)
        await self.notifier.error('⚠️ a', key='snapshot')
        self.t += 1800
        await self.notifier.error('⚠️ b', key='snapshot')
        self.t += 1799
        await self.notifier.error('⚠️ c', key='snapshot')
        self.assertEqual(self.sent(), [('⚠️ a', False)])
        self.t += 1
        await self.notifier.error('⚠️ d', key='snapshot')
        self.assertEqual(self.sent()[1], ('⚠️ d\n⏳ still failing after 1 h (4 tries)', True))

    async def test_episodes_are_per_key_and_the_default_key_is_shared(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.error('feed', key='sync')
        await self.notifier.error('snap', key='snapshot')
        await self.notifier.error('x')
        await self.notifier.error('y')
        self.assertEqual([text for text, _ in self.sent()], ['feed', 'snap', 'x'])

    async def test_resolved_sends_one_silent_recovery_then_the_next_failure_alerts_again(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.error('⚠️ snap', key='snapshot')
        self.t += 600
        await self.notifier.error('⚠️ snap', key='snapshot')
        self.t += 600
        await self.notifier.resolved('snapshot', '✅ snapshot applied again')
        await self.notifier.resolved('snapshot', '✅ snapshot applied again')       # nothing open: no-op
        await self.notifier.error('⚠️ snap', key='snapshot')
        self.assertEqual(self.sent(), [('⚠️ snap', False),
                                       ('✅ snapshot applied again (after 20 min, 2 failed tries)', True),
                                       ('⚠️ snap', False)])

    async def test_resolved_without_text_ends_the_episode_quietly(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.error('⚠️ cannot take over', key='cannot_take_over')
        await self.notifier.resolved('cannot_take_over')
        await self.notifier.error('⚠️ cannot take over', key='cannot_take_over')
        self.assertEqual(self.sent(), [('⚠️ cannot take over', False), ('⚠️ cannot take over', False)])

    async def test_confirmed_recovery_needs_a_clean_period(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.error('⚠️ feed', key='sync')
        for step, fails in ((10, False), (30, True), (10, False), (50, False)):
            self.t += step
            if fails:
                await self.notifier.error('⚠️ feed', key='sync')
            else:
                await self.notifier.resolved('sync', '✅ syncing again', confirm_sec=60)
        self.assertEqual(len(self.sent()), 1, 'a flapping feed does not announce a recovery')
        self.t += 10
        await self.notifier.resolved('sync', '✅ syncing again', confirm_sec=60)
        self.assertEqual(self.sent()[1], ('✅ syncing again (after 1 min, 2 failed tries)', True))

    async def test_unresolved_episode_ends_after_going_quiet(self):
        from dasovbot.constants import ERROR_EPISODE_IDLE_SEC
        self.notifier.attach_bot(self.bot)
        await self.notifier.error('⚠️ 409', key='conflict')
        self.t += ERROR_EPISODE_IDLE_SEC
        await self.notifier.error('⚠️ 409', key='conflict')
        self.assertEqual(self.sent(), [('⚠️ 409', False), ('⚠️ 409', False)])

    async def test_reminder_and_recovery_recorded_in_history(self):
        from tests.helpers import make_state
        state = make_state()
        notifier = DeveloperNotifier(make_config(developer_chat_id='42'), clock=lambda: self.t, state=state)
        await notifier.error('⚠️ a', key='k')
        self.t += 3600
        await notifier.error('⚠️ b', key='k')
        await notifier.resolved('k', '✅ ok')
        self.assertEqual([e['kind'] for e in state.ha_events], ['error', 'error', 'recovered'])
        self.assertIn('still failing', state.ha_events[1]['text'])

    async def test_send_failure_is_swallowed(self):
        self.notifier.attach_bot(self.bot)
        self.bot.send_message.side_effect = RuntimeError('telegram down')
        await self.notifier.transition('x')  # no raise
