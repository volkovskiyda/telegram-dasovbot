import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from backup import create_backup, prune_backups


class TestCreateBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, 'bot.db')
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('CREATE TABLE videos (url TEXT PRIMARY KEY, data TEXT)')
            conn.execute("INSERT INTO videos VALUES ('u1', 'd1')")

    def test_creates_readable_backup(self):
        backup_path = os.path.join(self.tmp.name, 'bot.db.backup_20260101_000000')
        create_backup(self.db_path, backup_path)
        self.assertTrue(os.path.exists(backup_path))
        with sqlite3.connect(backup_path) as conn:
            rows = conn.execute('SELECT url, data FROM videos').fetchall()
        self.assertEqual(rows, [('u1', 'd1')])

    def test_partial_file_removed_on_error(self):
        backup_path = os.path.join(self.tmp.name, 'bot.db.backup_20260101_000000')
        # sqlite3.Connection is an immutable C type, so fake the source
        # connection and keep the destination real to create the partial file
        mock_src = MagicMock()
        mock_src.backup.side_effect = sqlite3.Error('boom')
        real_connect = sqlite3.connect

        def fake_connect(path):
            return mock_src if path == self.db_path else real_connect(path)

        with patch('backup.sqlite3.connect', side_effect=fake_connect):
            with self.assertRaises(sqlite3.Error):
                create_backup(self.db_path, backup_path)
        self.assertFalse(os.path.exists(backup_path))


class TestPruneBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _touch(self, name: str) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, 'w') as f:
            f.write('x')
        return path

    def test_removes_oldest_beyond_max_count(self):
        names = [f'bot.db.backup_2026010{i}_000000' for i in range(1, 6)]
        for name in names:
            self._touch(name)
        prune_backups(self.tmp.name, max_count=3)
        remaining = sorted(os.listdir(self.tmp.name))
        self.assertEqual(remaining, names[2:])

    def test_skips_malformed_timestamp_names(self):
        self._touch('bot.db.backup_garbage')
        self._touch('bot.db.backup_20260101_000000')
        prune_backups(self.tmp.name, max_count=1)
        remaining = sorted(os.listdir(self.tmp.name))
        self.assertEqual(remaining, ['bot.db.backup_20260101_000000', 'bot.db.backup_garbage'])

    def test_tolerates_remove_errors(self):
        self._touch('bot.db.backup_20260101_000000')
        self._touch('bot.db.backup_20260102_000000')
        with patch('backup.os.remove', side_effect=OSError('denied')):
            prune_backups(self.tmp.name, max_count=1)  # must not raise


if __name__ == '__main__':
    unittest.main()
