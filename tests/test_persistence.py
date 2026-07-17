import unittest
from unittest.mock import patch

from dasovbot.persistence import remove, empty_media_folder_files


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


if __name__ == '__main__':
    unittest.main()
