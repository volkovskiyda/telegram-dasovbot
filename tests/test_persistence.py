import os
import tempfile
import time
import unittest
from unittest.mock import patch

from dasovbot.persistence import remove, empty_media_folder_files, remove_stale_media_files, move_atomic


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


class TestMoveAtomic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, 'media', 'v.mp4')
        self.dst = os.path.join(self.tmp.name, 'export', 'v.mp4')
        os.makedirs(os.path.dirname(self.src))
        with open(self.src, 'wb') as f:
            f.write(b'video')

    def test_same_filesystem_renames_without_leftover_partial(self):
        move_atomic(self.src, self.dst)
        self.assertFalse(os.path.exists(self.src))
        with open(self.dst, 'rb') as f:
            self.assertEqual(f.read(), b'video')
        self.assertEqual(os.listdir(os.path.dirname(self.dst)), ['v.mp4'])

    def test_cross_device_copies_then_removes_source(self):
        import errno
        real_rename = os.rename

        def rename(src, dst):
            if src == self.src:
                raise OSError(errno.EXDEV, 'Invalid cross-device link')
            return real_rename(src, dst)

        with patch('dasovbot.persistence.os.rename', side_effect=rename), \
                patch('dasovbot.persistence.shutil.copy2', wraps=__import__('shutil').copy2) as mock_copy:
            move_atomic(self.src, self.dst)
        mock_copy.assert_called_once_with(self.src, self.dst + '.partial')
        self.assertFalse(os.path.exists(self.src))
        with open(self.dst, 'rb') as f:
            self.assertEqual(f.read(), b'video')
        self.assertFalse(os.path.exists(self.dst + '.partial'))

    def test_failed_replace_removes_partial_and_reraises(self):
        with patch('dasovbot.persistence.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                move_atomic(self.src, self.dst)
        self.assertFalse(os.path.exists(self.dst))
        self.assertFalse(os.path.exists(self.dst + '.partial'))

    def test_missing_source_raises_and_leaves_nothing(self):
        os.remove(self.src)
        with self.assertRaises(FileNotFoundError):
            move_atomic(self.src, self.dst)
        self.assertFalse(os.path.exists(self.dst + '.partial'))
        self.assertFalse(os.path.exists(self.dst))
