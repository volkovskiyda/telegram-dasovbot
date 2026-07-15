import asyncio
import unittest
from unittest.mock import MagicMock, patch

import yt_dlp

import dasovbot.downloader as downloader
from dasovbot.downloader import extract_info
from dasovbot.models import VideoInfo, Intent, TemporaryInlineQuery
from tests.helpers import make_state, make_config


def make_raw_info(**overrides):
    raw = {
        'webpage_url': 'https://example.com/watch',
        'title': 'Raw Title',
        'duration': 42,
    }
    raw.update(overrides)
    return raw


class TestExtractInfo(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The module-level lock binds to the first event loop that acquires it;
        # each test runs in a fresh loop, so give each test a fresh lock.
        downloader._lock = asyncio.Lock()

    def _make_state(self, **overrides):
        return make_state(config=make_config(), **overrides)

    @patch('dasovbot.downloader.get_ydl')
    async def test_cached_with_file_id_skips_extraction(self, mock_get_ydl):
        cached = VideoInfo(title='cached', file_id='fid')
        state = self._make_state(videos={'q': cached})
        result = await extract_info('q', download=True, state=state)
        self.assertIs(result, cached)
        mock_get_ydl.assert_not_called()

    @patch('dasovbot.downloader.get_ydl')
    async def test_cached_without_file_id_no_download(self, mock_get_ydl):
        cached = VideoInfo(title='cached')
        state = self._make_state(videos={'q': cached})
        result = await extract_info('q', download=False, state=state)
        self.assertIs(result, cached)
        mock_get_ydl.assert_not_called()

    @patch('dasovbot.downloader.get_ydl')
    async def test_fresh_extraction_returns_processed_info(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.return_value = make_raw_info()
        state = self._make_state()
        result = await extract_info('q', download=False, state=state)
        self.assertEqual(result.title, 'Raw Title')
        self.assertEqual(result.webpage_url, 'https://example.com/watch')
        self.assertEqual(result.duration, 42)
        # Metadata-only extractions are not cached in state.videos
        self.assertNotIn('q', state.videos)

    @patch('dasovbot.downloader.get_ydl')
    async def test_dedup_via_canonical_url(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.return_value = make_raw_info(
            webpage_url='https://example.com/canonical',
        )
        canonical = VideoInfo(title='canonical', file_id='fid')
        state = self._make_state(videos={'https://example.com/canonical': canonical})
        result = await extract_info('https://short/q', download=True, state=state)
        self.assertIs(result, canonical)
        self.assertIs(state.videos['https://short/q'], canonical)

    @patch('dasovbot.downloader.get_ydl')
    async def test_video_error_marks_intent_ignored(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.side_effect = yt_dlp.DownloadError(
            'ERROR: Video unavailable'
        )
        intent = Intent()
        state = self._make_state(intents={'q': intent})
        result = await extract_info('q', download=False, state=state)
        self.assertIsNone(result)
        self.assertTrue(intent.ignored)

    @patch('dasovbot.downloader.get_ydl')
    async def test_video_error_marks_inline_query_ignored(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.side_effect = yt_dlp.DownloadError(
            'ERROR: Private video'
        )
        tiq = TemporaryInlineQuery()
        state = self._make_state(temporary_inline_queries={'q': tiq})
        result = await extract_info('q', download=False, state=state)
        self.assertIsNone(result)
        self.assertTrue(tiq.ignored)

    @patch('dasovbot.downloader.get_ydl')
    async def test_unrelated_download_error_not_ignored(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.side_effect = yt_dlp.DownloadError(
            'ERROR: network timeout'
        )
        intent = Intent()
        state = self._make_state(intents={'q': intent})
        result = await extract_info('q', download=False, state=state)
        self.assertIsNone(result)
        self.assertFalse(intent.ignored)

    @patch('dasovbot.downloader.get_ydl')
    async def test_generic_error_returns_none(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.side_effect = ValueError('boom')
        state = self._make_state()
        result = await extract_info('q', download=False, state=state)
        self.assertIsNone(result)

    @patch('dasovbot.downloader.get_ydl')
    async def test_download_populates_filepath(self, mock_get_ydl):
        cached = VideoInfo(title='cached')
        mock_get_ydl.return_value.extract_info.return_value = make_raw_info(
            requested_downloads=[{'filepath': '/media/v.webm', 'filename': 'v.webm'}],
        )
        state = self._make_state(videos={'q': cached})
        result = await extract_info('q', download=True, state=state)
        self.assertEqual(result.filepath, '/media/v.webm')
        self.assertEqual(result.filename, 'v.webm')
        mock_get_ydl.return_value.extract_info.assert_called_once_with('q', download=True)

    @patch('dasovbot.downloader.asyncio.wait_for', side_effect=asyncio.TimeoutError)
    @patch('dasovbot.downloader.get_ydl')
    async def test_download_timeout_returns_partial_info(self, mock_get_ydl, mock_wait):
        cached = VideoInfo(title='cached')
        state = self._make_state(videos={'q': cached})
        result = await extract_info('q', download=True, state=state)
        self.assertIs(result, cached)
        self.assertIsNone(result.file_id)

    @patch('dasovbot.downloader.get_ydl')
    async def test_download_error_returns_partial_info(self, mock_get_ydl):
        cached = VideoInfo(title='cached')
        mock_get_ydl.return_value.extract_info.side_effect = ValueError('boom')
        state = self._make_state(videos={'q': cached})
        result = await extract_info('q', download=True, state=state)
        self.assertIs(result, cached)


if __name__ == '__main__':
    unittest.main()
