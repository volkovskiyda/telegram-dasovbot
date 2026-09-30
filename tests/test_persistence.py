import os
import tempfile
import time
import unittest
from unittest.mock import patch

from dasovbot.persistence import remove, empty_media_folder_files, remove_stale_media_files


class TestRemove(unittest.TestCase):
    @patch('dasovbot.persistence.os.remove')
    def test_removes_file(self, mock_os_remove):
        remove('/tmp/file.mp4')
        mock_os_remove.assert_called_once_with('/tmp/file.mp4')

    @patch('dasovbot.persistence.os.remove', side_effect=OSError('fail'))
    def test_swallows_exception(self, mock_os_remove):
        remove('/tmp/file.mp4')


class TestEmptyMediaFolderFiles(unittest.TestCase):
    @patch('dasovbot.persistence.remove')
    @patch('dasovbot.persistence.os.listdir', return_value=['a.mp4', 'b.webm'])
    def test_removes_all_files(self, mock_listdir, mock_remove):
        empty_media_folder_files('/tmp/media')
        self.assertEqual(mock_remove.call_count, 2)
        mock_remove.assert_any_call('/tmp/media/a.mp4')
        mock_remove.assert_any_call('/tmp/media/b.webm')

    @patch('dasovbot.persistence.remove')
    @patch('dasovbot.persistence.os.listdir', return_value=[])
    def test_empty_folder(self, mock_listdir, mock_remove):
        empty_media_folder_files('/tmp/media')
        mock_remove.assert_not_called()


class TestRemoveStaleMediaFiles(unittest.TestCase):
    def test_removes_only_files_older_than_max_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('old.mp4', 'fresh.mp4.part'):
                with open(os.path.join(tmp, name), 'w') as f:
                    f.write('x')
            os.mkdir(os.path.join(tmp, 'subdir'))
            # ctime cannot be backdated, so move the cutoff instead
            with patch('dasovbot.persistence.time.time', return_value=time.time() + 7 * 3600):
                removed = remove_stale_media_files(tmp, 6 * 3600)
            self.assertEqual(sorted(removed), ['fresh.mp4.part', 'old.mp4'])
            self.assertEqual(os.listdir(tmp), ['subdir'])

    def test_keeps_recent_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'new.mp4')
            with open(path, 'w') as f:
                f.write('x')
            self.assertEqual(remove_stale_media_files(tmp, 6 * 3600), [])
            self.assertTrue(os.path.exists(path))

    def test_backdated_mtime_is_not_stale(self):
        # yt-dlp can set mtime from Last-Modified; ctime still says "just written"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'backdated.mp4')
            with open(path, 'w') as f:
                f.write('x')
            year_ago = time.time() - 365 * 86400
            os.utime(path, (year_ago, year_ago))
            self.assertEqual(remove_stale_media_files(tmp, 6 * 3600), [])
            self.assertTrue(os.path.exists(path))

    def test_missing_folder_returns_empty(self):
        self.assertEqual(remove_stale_media_files('/nonexistent/media', 60), [])


if __name__ == '__main__':
    unittest.main()
