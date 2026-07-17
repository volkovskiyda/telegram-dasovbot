import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from subscriptions import add_subscription, check_subscription, main
from dasovbot.database import SCHEMA


class SubscriptionsScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, 'bot.db')
        self.db = sqlite3.connect(self.db_path)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)

    def _insert(self, key, data):
        self.db.execute(
            "INSERT INTO subscriptions (key, data) VALUES (?, ?)", (key, json.dumps(data)))

    def _load(self, key):
        row = self.db.execute("SELECT data FROM subscriptions WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None


class TestCheckSubscription(SubscriptionsScriptTestCase):
    def test_missing_returns_false(self):
        self.assertFalse(check_subscription(self.db, '1', 'https://example.com/c/videos'))

    def test_already_subscribed(self):
        self._insert('https://example.com/c/videos', {'chat_ids': ['1'], 'title': 'T'})
        self.assertTrue(check_subscription(self.db, '1', 'https://example.com/c/videos'))
        self.assertEqual(self._load('https://example.com/c/videos')['chat_ids'], ['1'])

    def test_appends_new_chat_id(self):
        self._insert('https://example.com/c/videos', {'chat_ids': ['1'], 'title': 'T'})
        self.assertTrue(check_subscription(self.db, '2', 'https://example.com/c/videos'))
        self.assertEqual(self._load('https://example.com/c/videos')['chat_ids'], ['1', '2'])


class TestAddSubscription(SubscriptionsScriptTestCase):
    def _ydl(self, info=None, error=None):
        ydl = MagicMock()
        if error:
            ydl.extract_info.side_effect = error
        else:
            ydl.extract_info.return_value = info
        return ydl

    def test_creates_new_subscription(self):
        ydl = self._ydl({'uploader_url': 'https://example.com/c', 'title': 'T', 'uploader': 'U'})
        add_subscription(ydl, self.db, '1', 'https://example.com/c')
        data = self._load('https://example.com/c/videos')
        self.assertEqual(data['chat_ids'], ['1'])
        self.assertEqual(data['title'], 'T')

    def test_existing_videos_subscription_skips_extract(self):
        self._insert('https://example.com/c/videos', {'chat_ids': [], 'title': 'T'})
        ydl = self._ydl({})
        add_subscription(ydl, self.db, '1', 'https://example.com/c')
        ydl.extract_info.assert_not_called()
        self.assertEqual(self._load('https://example.com/c/videos')['chat_ids'], ['1'])

    def test_extract_failure_adds_nothing(self):
        ydl = self._ydl(error=ValueError('boom'))
        add_subscription(ydl, self.db, '1', 'https://example.com/c')
        count = self.db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
        self.assertEqual(count, 0)

    def test_null_title_falls_back_to_uploader(self):
        ydl = self._ydl({'uploader_url': 'https://example.com/c', 'title': None, 'uploader': 'U'})
        add_subscription(ydl, self.db, '1', 'https://example.com/c')
        self.assertEqual(self._load('https://example.com/c/videos')['title'], 'U')


class TestMain(SubscriptionsScriptTestCase):
    @patch('subscriptions.yt_dlp.YoutubeDL')
    @patch('subscriptions.load_config')
    def test_processes_urls_and_commits(self, mock_load, mock_ydl_cls):
        from tests.helpers import make_config
        mock_load.return_value = make_config(config_folder=self.tmp.name)
        mock_ydl_cls.return_value.extract_info.return_value = {
            'uploader_url': 'https://example.com/c', 'title': 'T', 'uploader': 'U',
        }
        urls_file = os.path.join(self.tmp.name, 'new_subscriptions.txt')
        with open(urls_file, 'w') as f:
            f.write('https://example.com/c\n\n')

        argv = ['subscriptions.py', '-u', '1', '-d', self.db_path, '-n', urls_file]
        with patch('sys.argv', argv):
            main()

        with sqlite3.connect(self.db_path) as check_db:
            row = check_db.execute(
                "SELECT data FROM subscriptions WHERE key = ?",
                ('https://example.com/c/videos',)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0])['chat_ids'], ['1'])

    @patch('subscriptions.yt_dlp.YoutubeDL')
    @patch('subscriptions.load_config')
    def test_without_user_prints_help(self, mock_load, mock_ydl_cls):
        from tests.helpers import make_config
        mock_load.return_value = make_config(config_folder=self.tmp.name)
        with patch('sys.argv', ['subscriptions.py']):
            main()
        mock_ydl_cls.assert_not_called()


if __name__ == '__main__':
    unittest.main()
