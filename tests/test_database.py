import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from tests.helpers import make_memory_db, make_config
from dasovbot.database import (
    init_db, migrate_from_json, warn_if_data_missing,
    upsert_video, delete_video, load_videos,
    upsert_intent, delete_intent, load_intents,
    upsert_user, load_users,
    upsert_subscription, delete_subscription, load_subscriptions,
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

    async def test_idempotent_schema(self):
        db = await make_memory_db()
        await db.executescript(SCHEMA)
        await db.commit()
        cursor = await db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='videos'")
        row = await cursor.fetchone()
        await db.close()
        self.assertEqual(row[0], 1)


class TestVideosCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_and_load(self):
        video = VideoInfo(title='Test', webpage_url='https://example.com')
        await upsert_video(self.db, 'key1', video)
        result = await load_videos(self.db)
        self.assertIn('key1', result)
        self.assertEqual(result['key1'].title, 'Test')

    async def test_delete(self):
        video = VideoInfo(title='Test')
        await upsert_video(self.db, 'key1', video)
        await delete_video(self.db, 'key1')
        result = await load_videos(self.db)
        self.assertNotIn('key1', result)

    async def test_overwrite(self):
        await upsert_video(self.db, 'k', VideoInfo(title='A'))
        await upsert_video(self.db, 'k', VideoInfo(title='B'))
        result = await load_videos(self.db)
        self.assertEqual(result['k'].title, 'B')

    async def test_load_empty(self):
        result = await load_videos(self.db)
        self.assertEqual(result, {})

    async def test_multiple_videos(self):
        await upsert_video(self.db, 'a', VideoInfo(title='A'))
        await upsert_video(self.db, 'b', VideoInfo(title='B'))
        result = await load_videos(self.db)
        self.assertEqual(len(result), 2)
        self.assertEqual(result['a'].title, 'A')
        self.assertEqual(result['b'].title, 'B')

    async def test_delete_nonexistent(self):
        await delete_video(self.db, 'nope')
        result = await load_videos(self.db)
        self.assertEqual(result, {})


class TestIntentsCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_and_load(self):
        intent = Intent(chat_ids=['1', '2'], priority=5)
        await upsert_intent(self.db, 'q1', intent)
        result = await load_intents(self.db)
        self.assertIn('q1', result)
        self.assertEqual(result['q1'].chat_ids, ['1', '2'])
        self.assertEqual(result['q1'].priority, 5)

    async def test_delete(self):
        await upsert_intent(self.db, 'q1', Intent())
        await delete_intent(self.db, 'q1')
        result = await load_intents(self.db)
        self.assertNotIn('q1', result)

    async def test_preserves_intent_messages(self):
        msgs = [IntentMessage(chat='c1', message='m1'), IntentMessage(chat='c2', message='m2')]
        intent = Intent(messages=msgs, source='sub')
        await upsert_intent(self.db, 'q', intent)
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
        await upsert_user(self.db, '100', {'name': 'Alice'})
        result = await load_users(self.db)
        self.assertIn('100', result)
        self.assertEqual(result['100']['name'], 'Alice')

    async def test_overwrite(self):
        await upsert_user(self.db, '1', {'v': 1})
        await upsert_user(self.db, '1', {'v': 2})
        result = await load_users(self.db)
        self.assertEqual(result['1']['v'], 2)

    async def test_load_empty(self):
        result = await load_users(self.db)
        self.assertEqual(result, {})


class TestSubscriptionsCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await make_memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_and_load(self):
        sub = Subscription(chat_ids=['1'], title='Channel')
        await upsert_subscription(self.db, 'url1', sub)
        result = await load_subscriptions(self.db)
        self.assertIn('url1', result)
        self.assertEqual(result['url1'].title, 'Channel')

    async def test_delete(self):
        await upsert_subscription(self.db, 'url1', Subscription())
        await delete_subscription(self.db, 'url1')
        result = await load_subscriptions(self.db)
        self.assertNotIn('url1', result)

    async def test_multiple(self):
        await upsert_subscription(self.db, 'a', Subscription(title='A'))
        await upsert_subscription(self.db, 'b', Subscription(title='B'))
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
        await upsert_video(self.db, 'existing', VideoInfo(title='X'))
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
        await upsert_video(self.db, 'k', VideoInfo(title='X'))
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
