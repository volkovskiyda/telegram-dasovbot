import unittest
from unittest.mock import patch, MagicMock

from dasovbot.downloader import _run_ffmpeg, convert_to_mp4


class TestRunFfmpeg(unittest.TestCase):

    @patch('dasovbot.downloader.os.path.getsize', return_value=1024)
    @patch('dasovbot.downloader.os.path.exists', return_value=True)
    @patch('dasovbot.downloader.subprocess.run')
    def test_success(self, mock_run, mock_exists, mock_size):
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_ffmpeg('/tmp/in.mkv', '/tmp/out.mp4', ['-c', 'copy'])
        self.assertTrue(result)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], 'ffmpeg')
        self.assertIn('-c', args)
        self.assertIn('copy', args)

    @patch('dasovbot.downloader.os.path.getsize', return_value=1024)
    @patch('dasovbot.downloader.os.path.exists', return_value=True)
    @patch('dasovbot.downloader.subprocess.run')
    def test_failure_nonzero_return(self, mock_run, mock_exists, mock_size):
        mock_run.return_value = MagicMock(returncode=1)
        result = _run_ffmpeg('/tmp/in.mkv', '/tmp/out.mp4', ['-c', 'copy'])
        self.assertFalse(result)

    @patch('dasovbot.downloader.os.path.exists', return_value=False)
    @patch('dasovbot.downloader.subprocess.run')
    def test_failure_no_output_file(self, mock_run, mock_exists):
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_ffmpeg('/tmp/in.mkv', '/tmp/out.mp4', ['-c', 'copy'])
        self.assertFalse(result)

    @patch('dasovbot.downloader.os.path.exists', return_value=False)
    @patch('dasovbot.downloader.subprocess.run', side_effect=__import__('subprocess').TimeoutExpired('ffmpeg', 600))
    def test_timeout(self, mock_run, mock_exists):
        result = _run_ffmpeg('/tmp/in.mkv', '/tmp/out.mp4', ['-c', 'copy'])
        self.assertFalse(result)

    @patch('dasovbot.downloader.os.path.getsize', return_value=0)
    @patch('dasovbot.downloader.os.path.exists', return_value=True)
    @patch('dasovbot.downloader.subprocess.run')
    def test_failure_empty_output(self, mock_run, mock_exists, mock_size):
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_ffmpeg('/tmp/in.mkv', '/tmp/out.mp4', ['-c', 'copy'])
        self.assertFalse(result)


class TestConvertToMp4(unittest.IsolatedAsyncioTestCase):

    async def test_already_mp4(self):
        result = await convert_to_mp4('/tmp/video.mp4')
        self.assertEqual(result, '/tmp/video.mp4')

    async def test_already_mp4_uppercase(self):
        result = await convert_to_mp4('/tmp/video.MP4')
        self.assertEqual(result, '/tmp/video.MP4')

    async def test_none_path(self):
        result = await convert_to_mp4(None)
        self.assertIsNone(result)

    @patch('dasovbot.downloader._cleanup_original')
    @patch('dasovbot.downloader._run_ffmpeg', return_value=True)
    async def test_remux_success(self, mock_ffmpeg, mock_cleanup):
        result = await convert_to_mp4('/tmp/video.mkv')
        self.assertEqual(result, '/tmp/video.mp4')
        mock_ffmpeg.assert_called_once_with('/tmp/video.mkv', '/tmp/video.mp4', ['-c', 'copy'])
        mock_cleanup.assert_called_once()

    @patch('dasovbot.downloader._cleanup_original')
    @patch('dasovbot.downloader.os.path.exists', return_value=False)
    @patch('dasovbot.downloader._run_ffmpeg', side_effect=[False, True])
    async def test_remux_fail_transcode_success(self, mock_ffmpeg, mock_exists, mock_cleanup):
        result = await convert_to_mp4('/tmp/video.webm')
        self.assertEqual(result, '/tmp/video.mp4')
        self.assertEqual(mock_ffmpeg.call_count, 2)
        second_call_args = mock_ffmpeg.call_args_list[1]
        self.assertEqual(second_call_args[0][2], ['-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac'])
        mock_cleanup.assert_called_once()

    @patch('dasovbot.downloader.os.path.exists', return_value=False)
    @patch('dasovbot.downloader._run_ffmpeg', return_value=False)
    async def test_both_fail_returns_original(self, mock_ffmpeg, mock_exists):
        result = await convert_to_mp4('/tmp/video.webm')
        self.assertEqual(result, '/tmp/video.webm')
        self.assertEqual(mock_ffmpeg.call_count, 2)


if __name__ == '__main__':
    unittest.main()
