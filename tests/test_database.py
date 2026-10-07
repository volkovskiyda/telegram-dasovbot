import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from tests.helpers import make_memory_db, make_config
from dasovbot.database import (
    migrate_schema_ha, get_meta, set_meta, load_rev, persist_rev, KEYED_TABLES, REV_TABLES,
    init_db, migrate_from_json, warn_if_data_missing,
    upsert_video, delete_video, load_videos,
    upsert_intent, delete_intent, load_intents,
    upsert_user, load_users,
    upsert_subscription, delete_subscription, load_subscriptions,
    insert_request, load_request_stats,
    upsert_banned_user, delete_banned_user, load_banned_users,
    SCHEMA,
)
from dasovbot.models import VideoInfo, Intent, IntentMessage, Subscription


class TestInitDb(unittest.IsolatedAsyncioTestCase):
    async def test_creates_all_tables(self):
        db = await make_memory_db()
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
        await db.close()
        self.assertIn('videos', tables)
        self.assertIn('intents', tables)
        self.assertIn('users', tables)
        self.assertIn('subscriptions', tables)
        self.assertIn('requests', tables)
        self.assertIn('banned_users', tables)
        self.assertIn('sync_meta', tables)
        self.assertIn('tombstones', tables)

    async def test_idempotent_schema(self):
        db = await make_memory_db()
        await db.executescript(SCHEMA)
        await db.commit()
        cursor = await db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='videos'")
        row = await cursor.fetchone()
        await db.close()
        self.assertEqual(row[0], 1)


class TestInitDbPragmas(unittest.IsolatedAsyncioTestCase):
    # A real file DB: journal_mode=WAL is a no-op on :memory: databases
    async def test_wal_and_busy_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = await init_db(os.path.join(tmp, 'bot.db'))
            try:
                cursor = await db.execute("PRAGMA journal_mode")
                self.assertEqual((await cursor.fetchone())[0], 'wal')
                cursor = await db.execute("PRAGMA busy_timeout")
                self.assertEqual((await cursor.fetchone())[0], 30000)
            finally:
                await db.close()

    async def test_wal_persists_for_later_connections(self):
        # journal_mode is stored in the DB file: backup.py's separate
        # connection must also see WAL without setting anything itself
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'bot.db')
            db = await init_db(db_path)
            await db.close()
            with sqlite3.connect(db_path) as raw:
                self.assertEqual(raw.execute("PRAGMA journal_mode").fetchone()[0], 'wal')


class TestVideosCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_and_load(self):
        video = VideoInfo(title='Test', webpage_url='https://example.com')
        await upsert_video(self.db, 'key1', video, rev=1)
        result = await load_videos(self.db)
        self.assertIn('key1', result)
        self.assertEqual(result['key1'].title, 'Test')

    async def test_delete(self):
        video = VideoInfo(title='Test')
        await upsert_video(self.db, 'key1', video, rev=1)
        await delete_video(self.db, 'key1', rev=2, deleted_at='20260101_000000')
        result = await load_videos(self.db)
        self.assertNotIn('key1', result)

    async def test_overwrite(self):
        await upsert_video(self.db, 'k', VideoInfo(title='A'), rev=1)
        await upsert_video(self.db, 'k', VideoInfo(title='B'), rev=1)
        result = await load_videos(self.db)
        self.assertEqual(result['k'].title, 'B')

    async def test_load_empty(self):
        result = await load_videos(self.db)
        self.assertEqual(result, {})

    async def test_multiple_videos(self):
        await upsert_video(self.db, 'a', VideoInfo(title='A'), rev=1)
        await upsert_video(self.db, 'b', VideoInfo(title='B'), rev=1)
        result = await load_videos(self.db)
        self.assertEqual(len(result), 2)
        self.assertEqual(result['a'].title, 'A')
        self.assertEqual(result['b'].title, 'B')

    async def test_delete_nonexistent(self):
        await delete_video(self.db, 'nope', rev=2, deleted_at='20260101_000000')
        result = await load_videos(self.db)
        self.assertEqual(result, {})


class TestIntentsCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_and_load(self):
        intent = Intent(chat_ids=['1', '2'], priority=5)
        await upsert_intent(self.db, 'q1', intent, rev=1)
        result = await load_intents(self.db)
        self.assertIn('q1', result)
        self.assertEqual(result['q1'].chat_ids, ['1', '2'])
        self.assertEqual(result['q1'].priority, 5)

    async def test_delete(self):
        await upsert_intent(self.db, 'q1', Intent(), rev=1)
        await delete_intent(self.db, 'q1', rev=2, deleted_at='20260101_000000')
        result = await load_intents(self.db)
        self.assertNotIn('q1', result)

    async def test_preserves_intent_messages(self):
        msgs = [IntentMessage(chat='c1', message='m1'), IntentMessage(chat='c2', message='m2')]
        intent = Intent(messages=msgs, source='sub')
        await upsert_intent(self.db, 'q', intent, rev=1)
        result = await load_intents(self.db)
        loaded = result['q']
        self.assertEqual(len(loaded.messages), 2)
        self.assertEqual(loaded.messages[0].chat, 'c1')
        self.assertEqual(loaded.messages[1].message, 'm2')
        self.assertEqual(loaded.source, 'sub')

    async def test_load_empty(self):
        result = await load_intents(self.db)
        self.assertEqual(result, {})


class TestUsersCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_and_load(self):
        await upsert_user(self.db, '100', {'name': 'Alice'}, rev=1)
        result = await load_users(self.db)
        self.assertIn('100', result)
        self.assertEqual(result['100']['name'], 'Alice')

    async def test_overwrite(self):
        await upsert_user(self.db, '1', {'v': 1}, rev=1)
        await upsert_user(self.db, '1', {'v': 2}, rev=1)
        result = await load_users(self.db)
        self.assertEqual(result['1']['v'], 2)

    async def test_load_empty(self):
        result = await load_users(self.db)
        self.assertEqual(result, {})


class TestBannedUsersCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_load_delete(self):
        await upsert_banned_user(self.db, '5', {'banned_at': '20260101_000000', 'name': 'Bob'}, rev=1)
        self.assertEqual(await load_banned_users(self.db), {'5': {'banned_at': '20260101_000000', 'name': 'Bob'}})
        await delete_banned_user(self.db, '5', rev=2, deleted_at='20260101_000000')
        self.assertEqual(await load_banned_users(self.db), {})


class TestRequestsLog(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_load_empty(self):
        self.assertEqual(await load_request_stats(self.db), ({}, {}))

    async def test_aggregates_requesters_and_counts(self):
        await insert_request(self.db, '2', 'u1', 'inline', '20260101_000000', rev=1)
        await insert_request(self.db, '1', 'u1', 'download', '20260102_000000', rev=1)
        await insert_request(self.db, '2', 'u1', 'inline', '20260103_000000', rev=1)
        await insert_request(self.db, '2', 'u2', 'inline', '20260104_000000', rev=1)

        video_requesters, user_requests = await load_request_stats(self.db)

        # First-request order, each user once per url
        self.assertEqual(video_requesters, {'u1': ['2', '1'], 'u2': ['2']})
        self.assertEqual(user_requests, {
            '1': {'count': 1, 'last_at': '20260102_000000'},
            '2': {'count': 3, 'last_at': '20260104_000000'},
        })


class TestSubscriptionsCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_and_load(self):
        sub = Subscription(chat_ids=['1'], title='Channel')
        await upsert_subscription(self.db, 'url1', sub, rev=1)
        result = await load_subscriptions(self.db)
        self.assertIn('url1', result)
        self.assertEqual(result['url1'].title, 'Channel')

    async def test_delete(self):
        await upsert_subscription(self.db, 'url1', Subscription(), rev=1)
        await delete_subscription(self.db, 'url1', rev=2, deleted_at='20260101_000000')
        result = await load_subscriptions(self.db)
        self.assertNotIn('url1', result)

    async def test_multiple(self):
        await upsert_subscription(self.db, 'a', Subscription(title='A'), rev=1)
        await upsert_subscription(self.db, 'b', Subscription(title='B'), rev=1)
        result = await load_subscriptions(self.db)
        self.assertEqual(len(result), 2)

    async def test_load_empty(self):
        result = await load_subscriptions(self.db)
        self.assertEqual(result, {})


class TestMigrateFromJson(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()
        self.config = make_config()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_skips_when_populated(self):
        await upsert_video(self.db, 'existing', VideoInfo(title='X'), rev=1)
        progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
        await migrate_from_json(self.db, self.config, progress)
        self.assertEqual(progress['status'], 'skipped')

    @patch('dasovbot.database.os.path.exists', return_value=False)
    async def test_skips_missing_files(self, mock_exists):
        progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
        await migrate_from_json(self.db, self.config, progress)
        self.assertEqual(progress['status'], 'skipped')

    @patch('dasovbot.database.os.rename')
    @patch('dasovbot.database.os.path.exists', return_value=True)
    @patch('builtins.open')
    async def test_migrates_from_json_files(self, mock_open, mock_exists, mock_rename):
        video_data = {'url1': {'title': 'Video 1', 'duration': 10}}
        mock_open.return_value.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(video_data))
        ))
        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps(video_data)
        mock_open.return_value.__enter__.return_value = mock_file
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
        await migrate_from_json(self.db, self.config, progress)
        self.assertEqual(progress['status'], 'completed')

    @patch('dasovbot.database.os.rename')
    @patch('dasovbot.database.os.path.exists', return_value=True)
    async def test_renames_files_after_migration(self, mock_exists, mock_rename):
        video_data = json.dumps({'url1': {'title': 'V', 'duration': 0}})
        files = {}

        def fake_open(path, *args, **kwargs):
            m = MagicMock()
            m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=video_data)))
            m.__exit__ = MagicMock(return_value=False)
            return m

        with patch('builtins.open', side_effect=fake_open):
            await migrate_from_json(self.db, self.config)

        self.assertTrue(mock_rename.called)

    @patch('dasovbot.database.os.rename')
    @patch('dasovbot.database.os.path.exists', return_value=True)
    async def test_batch_processing(self, mock_exists, mock_rename):
        large_data = {f'key{i}': {'title': f'Video {i}'} for i in range(600)}
        json_str = json.dumps(large_data)

        def fake_open(path, *args, **kwargs):
            m = MagicMock()
            m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=json_str)))
            m.__exit__ = MagicMock(return_value=False)
            return m

        with patch('builtins.open', side_effect=fake_open):
            progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
            await migrate_from_json(self.db, self.config, progress)

        self.assertEqual(progress['status'], 'completed')
        self.assertIn('videos', progress['tables'])
        self.assertEqual(progress['tables']['videos']['total'], 600)
        self.assertEqual(progress['tables']['videos']['done'], 600)

    async def test_progress_tracking(self):
        progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
        with patch('dasovbot.database.os.path.exists', return_value=False):
            await migrate_from_json(self.db, self.config, progress)
        self.assertIn(progress['status'], ('skipped', 'completed'))


class TestInitDbPath(unittest.IsolatedAsyncioTestCase):
    async def test_creates_parent_directory_and_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'data', 'bot.db')
            db = await init_db(db_path)
            try:
                cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {row[0] for row in await cursor.fetchall()}
            finally:
                await db.close()
            self.assertTrue(os.path.exists(db_path))
            self.assertLessEqual({'videos', 'intents', 'users', 'subscriptions'}, tables)


class TestMigrateFromJsonErrors(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = make_config(config_folder=self.tmp.name)
        os.makedirs(os.path.join(self.tmp.name, 'data'))

    async def asyncTearDown(self):
        await self.db.close()

    def _write(self, path, content):
        with open(path, 'w', encoding='utf8') as f:
            f.write(content)

    async def test_corrupt_json_logged_and_skipped(self):
        self._write(self.config.video_info_file, 'not valid json')
        progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
        with self.assertLogs('dasovbot.database', level='ERROR'):
            await migrate_from_json(self.db, self.config, progress)
        self.assertEqual(progress['status'], 'skipped')
        self.assertEqual(await load_videos(self.db), {})

    async def test_empty_json_file_skipped(self):
        self._write(self.config.video_info_file, '{}')
        progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
        await migrate_from_json(self.db, self.config, progress)
        self.assertEqual(progress['status'], 'skipped')
        self.assertTrue(os.path.exists(self.config.video_info_file))

    async def test_failed_table_file_preserved_while_good_file_renamed(self):
        self._write(self.config.video_info_file, json.dumps({'url1': {'title': 'V'}}))
        self._write(self.config.intent_info_file, 'not valid json')
        progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
        with self.assertLogs('dasovbot.database', level='ERROR'):
            await migrate_from_json(self.db, self.config, progress)
        # The good file is renamed out of the way...
        self.assertFalse(os.path.exists(self.config.video_info_file))
        # ...but the file that failed to migrate stays put so it can be retried.
        self.assertTrue(os.path.exists(self.config.intent_info_file))

    async def test_rename_failure_logged_but_migration_completes(self):
        self._write(self.config.video_info_file, json.dumps({'url1': {'title': 'V'}}))
        progress = {'status': 'pending', 'tables': {}, 'elapsed': 0.0}
        with patch('dasovbot.database.os.rename', side_effect=OSError('denied')):
            with self.assertLogs('dasovbot.database', level='ERROR'):
                await migrate_from_json(self.db, self.config, progress)
        self.assertEqual(progress['status'], 'completed')
        videos = await load_videos(self.db)
        self.assertEqual(videos['url1'].title, 'V')


class TestWarnIfDataMissing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = os.path.join(self.tmp.name, 'data')
        os.makedirs(self.data_dir)
        self.db_path = os.path.join(self.data_dir, 'bot.db')
        self.db = await init_db(self.db_path)

    async def asyncTearDown(self):
        await self.db.close()

    def _make_backup(self, name, populated):
        path = os.path.join(self.data_dir, name)
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)
            if populated:
                conn.execute("INSERT INTO videos (key, data) VALUES ('k', '{}')")
            conn.commit()
        return path

    async def test_warns_when_empty_but_backup_populated(self):
        self._make_backup('bot.db.backup_20260101_000000', populated=True)
        with self.assertLogs('dasovbot.database', level='WARNING') as cm:
            await warn_if_data_missing(self.db, self.db_path)
        self.assertIn('backup', ' '.join(cm.output).lower())

    async def test_silent_when_live_db_populated(self):
        await upsert_video(self.db, 'k', VideoInfo(title='X'), rev=1)
        self._make_backup('bot.db.backup_20260101_000000', populated=True)
        with self.assertNoLogs('dasovbot.database', level='WARNING'):
            await warn_if_data_missing(self.db, self.db_path)

    async def test_silent_when_no_backups(self):
        with self.assertNoLogs('dasovbot.database', level='WARNING'):
            await warn_if_data_missing(self.db, self.db_path)

    async def test_silent_when_backups_also_empty(self):
        self._make_backup('bot.db.backup_20260101_000000', populated=False)
        with self.assertNoLogs('dasovbot.database', level='WARNING'):
            await warn_if_data_missing(self.db, self.db_path)

    async def test_ignores_unreadable_backup(self):
        bad = os.path.join(self.data_dir, 'bot.db.backup_20260101_000000')
        with open(bad, 'w') as f:
            f.write('not a sqlite file')
        with self.assertNoLogs('dasovbot.database', level='WARNING'):
            await warn_if_data_missing(self.db, self.db_path)


if __name__ == '__main__':
    unittest.main()


OLD_SCHEMA = """
CREATE TABLE videos (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE intents (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE users (chat_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE subscriptions (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE banned_users (user_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, url TEXT NOT NULL,
    source TEXT, requested_at TEXT NOT NULL);
"""


class TestMigrateSchemaHa(unittest.IsolatedAsyncioTestCase):
    """Migration of a pre-HA database file (no rev columns, duplicate requests)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, 'bot.db')
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(OLD_SCHEMA)
            conn.execute("INSERT INTO videos (key, data) VALUES ('v1', '{}'), ('v2', '{}')")
            conn.execute("INSERT INTO intents (key, data) VALUES ('q1', '{}')")
            conn.execute("INSERT INTO users (chat_id, data) VALUES ('1', '{}')")
            conn.execute("INSERT INTO subscriptions (key, data) VALUES ('s1', '{}')")
            conn.execute("INSERT INTO banned_users (user_id, data) VALUES ('9', '{}')")
            for row in [('1', 'u', 'inline', 't1'), ('1', 'u', 'inline', 't1'), ('1', 'u', 'inline', 't2')]:
                conn.execute("INSERT INTO requests (user_id, url, source, requested_at) VALUES (?, ?, ?, ?)", row)
            conn.commit()

    async def _all_revs(self, db):
        revs = {}
        for table in REV_TABLES:
            cursor = await db.execute(f"SELECT rev FROM {table}")
            revs[table] = sorted(r[0] for r in await cursor.fetchall())
        return revs

    async def test_adds_rev_columns_and_backfills_unique_revs(self):
        db = await init_db(self.db_path)
        try:
            revs = await self._all_revs(db)
            flat = [r for table in revs.values() for r in table]
            self.assertTrue(all(r > 0 for r in flat))
            self.assertEqual(len(flat), len(set(flat)), 'revs must be unique across tables')
            self.assertEqual(await load_rev(db), max(flat))
            cursor = await db.execute("SELECT COUNT(*) FROM requests")
            self.assertEqual((await cursor.fetchone())[0], 2, 'exact duplicate purged')
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
            names = {r[0] for r in await cursor.fetchall()}
            self.assertIn('idx_requests_dedupe', names)
            self.assertIn('idx_videos_rev', names)
            self.assertIn('idx_tombstones_rev', names)
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            self.assertTrue({'sync_meta', 'tombstones'} <= {r[0] for r in await cursor.fetchall()})
        finally:
            await db.close()

    async def test_second_run_is_a_noop(self):
        db = await init_db(self.db_path)
        before = await self._all_revs(db)
        counter = await load_rev(db)
        await db.close()
        db = await init_db(self.db_path)
        try:
            self.assertEqual(await self._all_revs(db), before)
            self.assertEqual(await load_rev(db), counter)
        finally:
            await db.close()

    async def test_dedupe_index_rejects_identical_request(self):
        db = await init_db(self.db_path)
        try:
            await insert_request(db, '1', 'u', 'inline', 't1', rev=100)
            cursor = await db.execute("SELECT COUNT(*) FROM requests WHERE requested_at = 't1'")
            self.assertEqual((await cursor.fetchone())[0], 1)
        finally:
            await db.close()


class TestSyncMeta(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_get_set_meta_roundtrip_json(self):
        self.assertIsNone(await get_meta(self.db, 'missing'))
        self.assertEqual(await get_meta(self.db, 'missing', 7), 7)
        await set_meta(self.db, 'k', {'a': [1, 2]})
        await set_meta(self.db, 'k', {'a': [3]})
        self.assertEqual(await get_meta(self.db, 'k'), {'a': [3]})

    async def test_persist_rev_never_lowers(self):
        self.assertEqual(await load_rev(self.db), 0)
        await persist_rev(self.db, 10)
        await persist_rev(self.db, 5)
        self.assertEqual(await load_rev(self.db), 10)
        await persist_rev(self.db, 11)
        self.assertEqual(await load_rev(self.db), 11)

    async def test_keyed_tables_cover_rev_tables(self):
        self.assertEqual(set(REV_TABLES), set(KEYED_TABLES) | {'requests'})


class TestRevStampedWrites(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def _rev(self, table, column, key):
        cursor = await self.db.execute(f"SELECT rev FROM {table} WHERE {column} = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def _tombstones(self):
        cursor = await self.db.execute("SELECT tbl, key, rev, deleted_at FROM tombstones ORDER BY rev")
        return await cursor.fetchall()

    async def test_upsert_stores_rev_and_raises_counter(self):
        await upsert_video(self.db, 'k', VideoInfo(title='A'), rev=5)
        self.assertEqual(await self._rev('videos', 'key', 'k'), 5)
        self.assertEqual(await load_rev(self.db), 5)
        await upsert_user(self.db, '1', {'v': 1}, rev=6)
        await upsert_banned_user(self.db, '9', {'name': 'E'}, rev=7)
        await upsert_subscription(self.db, 's', Subscription(), rev=8)
        await upsert_intent(self.db, 'q', Intent(), rev=9)
        await insert_request(self.db, '1', 'u', 'inline', 't', rev=10)
        self.assertEqual(await self._rev('users', 'chat_id', '1'), 6)
        self.assertEqual(await self._rev('banned_users', 'user_id', '9'), 7)
        self.assertEqual(await self._rev('subscriptions', 'key', 's'), 8)
        self.assertEqual(await self._rev('intents', 'key', 'q'), 9)
        self.assertEqual(await self._rev('requests', 'url', 'u'), 10)
        self.assertEqual(await load_rev(self.db), 10)

    async def test_delete_writes_tombstone_only_for_existing_key(self):
        await upsert_intent(self.db, 'q', Intent(), rev=1)
        await delete_intent(self.db, 'q', rev=2, deleted_at='20260101_000000')
        await delete_intent(self.db, 'never', rev=3, deleted_at='20260101_000001')
        self.assertEqual(await self._tombstones(), [('intents', 'q', 2, '20260101_000000')])
        self.assertEqual(await load_rev(self.db), 3)
        self.assertEqual(await load_intents(self.db), {})

    async def test_reupsert_clears_tombstone(self):
        await upsert_subscription(self.db, 's', Subscription(), rev=1)
        await delete_subscription(self.db, 's', rev=2, deleted_at='20260101_000000')
        self.assertEqual(len(await self._tombstones()), 1)
        await upsert_subscription(self.db, 's', Subscription(title='back'), rev=3)
        self.assertEqual(await self._tombstones(), [])
        self.assertEqual(await self._rev('subscriptions', 'key', 's'), 3)

    async def test_every_keyed_table_tombstones(self):
        await upsert_video(self.db, 'v', VideoInfo(title='V'), rev=1)
        await upsert_user(self.db, 'u', {}, rev=2)
        await upsert_banned_user(self.db, 'b', {}, rev=3)
        await delete_video(self.db, 'v', rev=4, deleted_at='t')
        await delete_banned_user(self.db, 'b', rev=5, deleted_at='t')
        self.assertEqual([(t, k) for t, k, _, _ in await self._tombstones()],
                         [('videos', 'v'), ('banned_users', 'b')])
