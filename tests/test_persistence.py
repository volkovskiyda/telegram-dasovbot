import json
import unittest
from unittest.mock import patch, mock_open, MagicMock

from dasovbot.persistence import remove, write_file, read_file, empty_media_folder_files


class TestRemove(unittest.TestCase):
    @patch('dasovbot.persistence.os.remove')
    def test_removes_file(self, mock_os_remove):
        remove('/tmp/file.mp4')
        mock_os_remove.assert_called_once_with('/tmp/file.mp4')

    @patch('dasovbot.persistence.os.remove', side_effect=OSError('fail'))
    def test_swallows_exception(self, mock_os_remove):
        remove('/tmp/file.mp4')


class TestWriteFile(unittest.TestCase):
    @patch('builtins.open', new_callable=mock_open)
    def test_writes_json(self, mock_file):
        data = {'key': 'value'}
        write_file('/tmp/test.json', data)
        mock_file.assert_called_once_with('/tmp/test.json', 'w', encoding='utf8')
        handle = mock_file()
        written = ''.join(call.args[0] for call in handle.write.call_args_list)
        self.assertIn('key', written)

    @patch('builtins.open', side_effect=IOError('disk full'))
    def test_error_handling(self, mock_file):
        write_file('/tmp/test.json', {'k': 'v'})


class TestReadFile(unittest.TestCase):
    def test_reads_json(self):
        data = {'key': 'value'}
        m = mock_open(read_data=json.dumps(data))
        with patch('builtins.open', m):
            result = read_file('/tmp/test.json', {})
        self.assertEqual(result, data)

    @patch('dasovbot.persistence.write_file')
    @patch('builtins.open', side_effect=FileNotFoundError('not found'))
    def test_error_returns_empty_and_writes_default(self, mock_file, mock_write):
        result = read_file('/tmp/missing.json', {'default': True})
        self.assertEqual(result, {})
        mock_write.assert_called_once_with('/tmp/missing.json', {'default': True})


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
