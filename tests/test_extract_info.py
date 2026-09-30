import asyncio
import os
import tempfile
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
        # The abandoned executor thread may still be writing the output path:
        # the intent must be held back for a full timeout window
        import time
        from dasovbot.constants import TIMEOUT_SEC
        self.assertGreater(state.intent_retry_after['q'], time.monotonic() + TIMEOUT_SEC - 60)

    @patch('dasovbot.downloader.asyncio.wait_for', side_effect=asyncio.TimeoutError)
    @patch('dasovbot.downloader.get_ydl')
    async def test_metadata_timeout_returns_none(self, mock_get_ydl, mock_wait):
        state = self._make_state()
        result = await extract_info('q', download=True, state=state)
        self.assertIsNone(result)
        # The download step must not run after a metadata timeout
        for call in mock_get_ydl.return_value.extract_info.call_args_list:
            self.assertFalse(call.kwargs.get('download'))

    @patch('dasovbot.downloader.yt_dlp.YoutubeDL')
    async def test_download_with_opts_serializes_behind_lock(self, mock_ydl_cls):
        from dasovbot import downloader
        mock_ydl_cls.return_value.extract_info.return_value = {'id': '1'}
        # The fallback path must queue behind the primary download, never
        # overlap with it
        await downloader._lock.acquire()
        task = asyncio.create_task(downloader.download_with_opts({}, 'q'))
        try:
            for _ in range(5):
                await asyncio.sleep(0)
            mock_ydl_cls.assert_not_called()
        finally:
            downloader._lock.release()
        result = await task
        self.assertEqual(result, {'id': '1'})
        mock_ydl_cls.return_value.close.assert_called_once()

    @patch('dasovbot.downloader.asyncio.wait_for', side_effect=asyncio.TimeoutError)
    @patch('dasovbot.downloader.yt_dlp.YoutubeDL')
    async def test_download_with_opts_timeout_propagates(self, mock_ydl_cls, mock_wait):
        from dasovbot import downloader
        with self.assertRaises(asyncio.TimeoutError):
            await downloader.download_with_opts({}, 'q')

    @patch('dasovbot.downloader.get_ydl')
    async def test_metadata_error_skips_download(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.side_effect = ValueError('boom')
        state = self._make_state()
        result = await extract_info('q', download=True, state=state)
        self.assertIsNone(result)
        mock_get_ydl.return_value.extract_info.assert_called_once_with('q', download=False)

    @patch('dasovbot.downloader.get_ydl')
    async def test_download_error_returns_partial_info(self, mock_get_ydl):
        cached = VideoInfo(title='cached')
        mock_get_ydl.return_value.extract_info.side_effect = ValueError('boom')
        state = self._make_state(videos={'q': cached})
        result = await extract_info('q', download=True, state=state)
        self.assertIs(result, cached)


class TestDownloadAttempt(unittest.TestCase):
    def setUp(self):
        downloader._path_owners.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _touch(self, name):
        path = os.path.join(self.tmp.name, name)
        with open(path, 'w') as f:
            f.write('x')
        return path

    def test_progress_hook_raises_once_cancelled(self):
        attempt = downloader.DownloadAttempt()
        attempt._progress_hook({'filename': '/media/v.mp4'})
        attempt.cancel()
        with self.assertRaises(yt_dlp.utils.DownloadCancelled):
            attempt._progress_hook({'filename': '/media/v.mp4'})
        with self.assertRaises(yt_dlp.utils.DownloadCancelled):
            attempt._postprocessor_hook({'info_dict': {'filepath': '/media/v.mp4'}})

    def test_failed_attempt_removes_part_fragments_and_state(self):
        part = self._touch('v.mp4.part')
        fragments = [self._touch('v.mp4.part-Frag1'), self._touch('v.mp4.part-Frag2.part')]
        state_file = self._touch('v.mp4.part.ytdl')
        other = self._touch('other.mp4')
        attempt = downloader.DownloadAttempt()
        attempt._progress_hook({'tmpfilename': part, 'filename': part[:-5]})
        attempt.finish(succeeded=False)
        for path in [part, state_file, *fragments]:
            self.assertFalse(os.path.exists(path), path)
        self.assertTrue(os.path.exists(other))
        self.assertEqual(downloader._path_owners, {})

    def test_successful_attempt_keeps_file(self):
        video = self._touch('v.mp4')
        attempt = downloader.DownloadAttempt()
        attempt._postprocessor_hook({'info_dict': {'filepath': video}})
        attempt.finish(succeeded=True)
        self.assertTrue(os.path.exists(video))
        self.assertEqual(downloader._path_owners, {})

    def test_success_after_cancel_is_discarded(self):
        # Finished in the background after the caller timed out: nothing
        # would ever send or remove it
        video = self._touch('v.mp4')
        attempt = downloader.DownloadAttempt()
        attempt._postprocessor_hook({'info_dict': {'filepath': video}})
        attempt.cancel()
        attempt.finish(succeeded=True)
        self.assertFalse(os.path.exists(video))

    def test_cancel_after_success_discards_file(self):
        # wait_for timed out in the same instant the thread succeeded: the
        # caller never sees the result, so the kept file must go too
        video = self._touch('v.mp4')
        attempt = downloader.DownloadAttempt()
        attempt._postprocessor_hook({'info_dict': {'filepath': video}})
        attempt.finish(succeeded=True)
        self.assertTrue(os.path.exists(video))
        attempt.cancel()
        self.assertFalse(os.path.exists(video))
        # Idempotent: a second cancel has nothing left to remove
        attempt.cancel()

    def test_cancel_after_success_spares_paths_a_newer_attempt_claimed(self):
        video = self._touch('v.mp4')
        done = downloader.DownloadAttempt()
        done._postprocessor_hook({'info_dict': {'filepath': video}})
        done.finish(succeeded=True)
        retry = downloader.DownloadAttempt()
        retry._postprocessor_hook({'info_dict': {'filepath': video}})
        done.cancel()
        self.assertTrue(os.path.exists(video))

    def test_late_cleanup_spares_paths_a_newer_attempt_claimed(self):
        # A YouTube retry renders the same output path as the abandoned attempt
        part = self._touch('v.mp4.part')
        abandoned = downloader.DownloadAttempt()
        abandoned._progress_hook({'tmpfilename': part})
        abandoned.cancel()
        retry = downloader.DownloadAttempt()
        retry._progress_hook({'tmpfilename': part})
        abandoned.finish(succeeded=False)
        self.assertTrue(os.path.exists(part))
        self.assertIs(downloader._path_owners[part], retry)

    def test_extract_info_sync_attaches_hooks_and_finishes(self):
        attempt = MagicMock()
        with patch('dasovbot.downloader.get_ydl') as mock_get_ydl:
            mock_get_ydl.return_value.extract_info.side_effect = ValueError('boom')
            with self.assertRaises(ValueError):
                downloader.extract_info_sync('q', download=True, attempt=attempt)
        attempt.attach.assert_called_once_with(mock_get_ydl.return_value)
        attempt.finish.assert_called_once_with(False)
        mock_get_ydl.return_value.close.assert_called_once()

    @patch('dasovbot.downloader.yt_dlp.YoutubeDL')
    def test_extract_info_opts_sync_shares_the_attempt_lifecycle(self, mock_ydl_cls):
        attempt = MagicMock()
        mock_ydl_cls.return_value.extract_info.return_value = {'id': '1'}
        result = downloader._extract_info_opts_sync({'format': 'x'}, 'q', attempt)
        self.assertEqual(result, {'id': '1'})
        mock_ydl_cls.assert_called_once_with({'format': 'x'})
        mock_ydl_cls.return_value.extract_info.assert_called_once_with('q', download=True)
        attempt.attach.assert_called_once_with(mock_ydl_cls.return_value)
        attempt.finish.assert_called_once_with(True)
        mock_ydl_cls.return_value.close.assert_called_once()


class TestRunDownload(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.downloader.asyncio.wait_for', side_effect=asyncio.TimeoutError)
    async def test_timeout_cancels_attempt(self, mock_wait):
        attempt = downloader.DownloadAttempt()
        with self.assertRaises(asyncio.TimeoutError):
            await downloader._run_download(lambda: None, attempt)
        self.assertTrue(attempt.cancelled)

    async def test_success_leaves_attempt_running(self):
        attempt = downloader.DownloadAttempt()
        self.assertEqual(await downloader._run_download(lambda: 'ok', attempt), 'ok')
        self.assertFalse(attempt.cancelled)


if __name__ == '__main__':
    unittest.main()
