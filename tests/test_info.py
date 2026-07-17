import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import info as info_module
from info import sizeof_fmt, video
from tests.helpers import make_config


def make_raw_video(**overrides):
    raw = {
        'title': 'Title',
        'description': 'D' * 100,
        'webpage_url': 'https://example.com/v',
        'duration': 90,
        'format': '720p',
        'filesize': 2048,
        'url': 'https://cdn.example.com/v.mp4',
        'thumbnail': 'https://example.com/t.jpg',
        'upload_date': '20260101',
        'channel_url': 'https://example.com/channel',
        'uploader': 'Uploader',
        'uploader_url': 'https://example.com/@uploader',
        'live_status': 'not_live',
        'is_live': False,
        'was_live': False,
    }
    raw.update(overrides)
    return raw


class TestSizeofFmt(unittest.TestCase):
    def test_zero_is_not_available(self):
        self.assertEqual(sizeof_fmt(0), 'N/A')

    def test_bytes(self):
        self.assertEqual(sizeof_fmt(512), '512.0B')

    def test_kibibytes(self):
        self.assertEqual(sizeof_fmt(1024), '1.0KiB')

    def test_fractional_kibibytes(self):
        self.assertEqual(sizeof_fmt(1536), '1.5KiB')

    def test_gibibytes(self):
        self.assertEqual(sizeof_fmt(1024 ** 3), '1.0GiB')

    def test_negative_value_keeps_sign(self):
        self.assertEqual(sizeof_fmt(-512), '-512.0B')

    def test_huge_value_uses_yobibytes(self):
        self.assertEqual(sizeof_fmt(1024 ** 8), '1.0YiB')


class TestVideo(unittest.TestCase):
    def test_maps_fields(self):
        result = video(make_raw_video())
        self.assertEqual(result['title'], 'Title')
        self.assertEqual(result['description'], 'D' * 50)
        self.assertEqual(result['url'], 'https://example.com/v')
        self.assertEqual(result['download'], 'https://cdn.example.com/v.mp4')
        self.assertEqual(result['duration'], 90)
        self.assertEqual(result['filesize'], '2.0KiB')
        self.assertEqual(result['uploader'], 'Uploader')
        self.assertEqual(result['upload_date'], '20260101')

    def test_missing_optional_fields_default(self):
        raw = make_raw_video(duration=None, filesize=None)
        result = video(raw)
        self.assertEqual(result['duration'], 0)
        self.assertEqual(result['filesize'], 'N/A')


class TestInfoCommand(unittest.IsolatedAsyncioTestCase):
    def _make_ydl(self, extracted):
        ydl = MagicMock()
        ydl.extract_info.return_value = extracted
        return ydl

    @patch('info.json_dumps')
    @patch('info.make_ydl')
    async def test_single_video(self, mock_make_ydl, mock_dumps):
        mock_make_ydl.return_value = self._make_ydl(make_raw_video())
        await info_module.info('https://example.com/v', download=False)
        mock_make_ydl.return_value.extract_info.assert_called_once_with(
            'https://example.com/v', download=False)
        output = mock_dumps.call_args[0][0]
        self.assertEqual(output['title'], 'Title')

    @patch('info.json_dumps')
    @patch('info.make_ydl')
    async def test_playlist_entries_reversed(self, mock_make_ydl, mock_dumps):
        entries = [make_raw_video(title='A'), make_raw_video(title='B')]
        mock_make_ydl.return_value = self._make_ydl({'entries': entries})
        await info_module.info('https://example.com/playlist', download=True)
        output = mock_dumps.call_args[0][0]
        self.assertEqual([item['title'] for item in output], ['B', 'A'])

    @patch('info.json_dumps')
    @patch('info.make_ydl')
    async def test_nested_entries_unwrapped(self, mock_make_ydl, mock_dumps):
        nested = [make_raw_video(title='N1'), make_raw_video(title='N2')]
        mock_make_ydl.return_value = self._make_ydl({'entries': [{'entries': nested}]})
        await info_module.info('https://example.com/channel', download=False)
        output = mock_dumps.call_args[0][0]
        self.assertEqual([item['title'] for item in output], ['N2', 'N1'])


class TestJsonDumps(unittest.TestCase):
    def test_prints_json(self):
        import contextlib
        import io
        import json
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            info_module.json_dumps({'a': 1})
        self.assertEqual(json.loads(buf.getvalue()), {'a': 1})


class TestMakeYdl(unittest.TestCase):
    @patch('info.yt_dlp.YoutubeDL')
    @patch('info.load_config')
    def test_drops_quiet_option(self, mock_load, mock_ydl_cls):
        mock_load.return_value = make_config()
        info_module.make_ydl()
        opts = mock_ydl_cls.call_args[0][0]
        self.assertNotIn('quiet', opts)


class TestMain(unittest.TestCase):
    @patch('info.info', new_callable=AsyncMock)
    def test_parses_url_and_download_flag(self, mock_info):
        with patch('sys.argv', ['info.py', 'https://example.com/v', '--download']):
            info_module.main()
        mock_info.assert_awaited_once_with('https://example.com/v', download=True)

    @patch('info.info', new_callable=AsyncMock)
    def test_download_defaults_to_false(self, mock_info):
        with patch('sys.argv', ['info.py', 'https://example.com/v']):
            info_module.main()
        mock_info.assert_awaited_once_with('https://example.com/v', download=False)


if __name__ == '__main__':
    unittest.main()
