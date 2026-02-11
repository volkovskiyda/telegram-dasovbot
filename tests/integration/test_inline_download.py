"""
Integration tests for inline functionality with real video extraction and download.

These tests exercise the inline flow with real yt-dlp extraction and real
Telegram API calls. extract_info is called against a real video URL; only
inline_query.answer is mocked (can't trigger inline queries programmatically).

Requires .env.test with TEST_BOT_TOKEN, TEST_USER_ID, TEST_CHAT_ID,
and a valid TEST_VIDEO_URL (not the default example.com placeholder).
"""
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4

from telegram import InlineQueryResultCachedVideo

from dasovbot.downloader import extract_info, extract_url, process_info, get_ydl
from dasovbot.handlers.inline import inline_video, inline_query_handler, chosen_query
from dasovbot.helpers import now
from dasovbot.models import VideoInfo, TemporaryInlineQuery
from tests.integration.base import IntegrationTestBase


def _requires_real_video(test_config):
    """Skip if TEST_VIDEO_URL is the default placeholder"""
    url = test_config.test_video_url
    if not url or 'example.com' in url:
        raise unittest.SkipTest(
            'TEST_VIDEO_URL must be a real video URL (not example.com)'
        )


class TestExtractInfo(IntegrationTestBase):
    """Real yt-dlp extraction against TEST_VIDEO_URL"""

    def setUp(self):
        _requires_real_video(self.test_config)

    async def test_extract_info_no_download(self):
        """extract_info(download=False) returns valid VideoInfo"""
        url = self.test_config.test_video_url
        info = await extract_info(url, download=False, state=self.state)

        self.assertIsNotNone(info)
        self.assertIsInstance(info, VideoInfo)
        self.assertIsNotNone(info.title)
        self.assertTrue(len(info.title) > 0)
        self.assertIsNotNone(info.caption)

    async def test_extract_info_returns_cached_from_state(self):
        """extract_info returns cached VideoInfo when pre-populated in state"""
        url = self.test_config.test_video_url
        cached = VideoInfo(title='Cached', webpage_url=url)
        self.state.videos[url] = cached

        info = await extract_info(url, download=False, state=self.state)

        self.assertIs(info, cached)

    async def test_extract_info_consistent_results(self):
        """Two extract_info calls return equivalent VideoInfo"""
        url = self.test_config.test_video_url
        info1 = await extract_info(url, download=False, state=self.state)
        info2 = await extract_info(url, download=False, state=self.state)

        self.assertEqual(info1.title, info2.title)
        self.assertEqual(info1.webpage_url, info2.webpage_url)

    async def test_extract_info_invalid_url(self):
        """extract_info returns None for an invalid URL"""
        info = await extract_info(
            'https://www.youtube.com/watch?v=INVALID_ID_000',
            download=False,
            state=self.state,
        )
        self.assertIsNone(info)

    async def test_process_info_raw(self):
        """process_info transforms raw ydl dict into VideoInfo"""
        url = self.test_config.test_video_url
        ydl = get_ydl()
        raw = ydl.extract_info(url, download=False)

        info = process_info(raw)

        self.assertIsInstance(info, VideoInfo)
        self.assertTrue(len(info.title) > 0)
        self.assertIsNotNone(extract_url(info))


class TestInlineVideo(IntegrationTestBase):
    """Test inline_video() builds correct result from real VideoInfo"""

    def setUp(self):
        _requires_real_video(self.test_config)

    async def test_inline_video_without_file_id(self):
        """inline_video with no file_id uses animation_file_id and adds reply_markup"""
        url = self.test_config.test_video_url
        info = await extract_info(url, download=False, state=self.state)

        self.state.animation_file_id = 'test_anim_id'
        inline_queries = {}
        result = inline_video(info, inline_queries, self.state.animation_file_id)

        self.assertIsInstance(result, InlineQueryResultCachedVideo)
        self.assertEqual(result.video_file_id, 'test_anim_id')
        self.assertEqual(result.title, info.title)
        self.assertIsNotNone(result.reply_markup)
        self.assertEqual(len(inline_queries), 1)

    async def test_inline_video_with_file_id(self):
        """inline_video with file_id uses it directly, no reply_markup"""
        url = self.test_config.test_video_url
        info = await extract_info(url, download=False, state=self.state)
        info.file_id = 'cached_file_123'

        inline_queries = {}
        result = inline_video(info, inline_queries, 'anim_id')

        self.assertEqual(result.video_file_id, 'cached_file_123')
        self.assertIsNone(result.reply_markup)


class TestInlineQueryHandler(IntegrationTestBase):
    """Full inline_query_handler with real extract_info, mocked query.answer"""

    def setUp(self):
        _requires_real_video(self.test_config)

    async def test_handler_extracts_and_answers(self):
        """Handler calls extract_info, builds results, calls answer"""
        url = self.test_config.test_video_url
        self.state.animation_file_id = 'test_anim_id'

        mock_query = AsyncMock()
        mock_query.query = url
        mock_query.from_user = MagicMock(id=self.test_config.user_id)
        mock_query.answer = AsyncMock()

        update = MagicMock()
        update.inline_query = mock_query

        context = MagicMock()
        context.bot_data = {'state': self.state}
        context.user_data = {}

        await inline_query_handler(update, context)

        mock_query.answer.assert_called_once()
        call_kwargs = mock_query.answer.call_args
        results = call_kwargs.kwargs.get('results') or call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs['results']
        self.assertGreater(len(results), 0)
        self.assertIsInstance(results[0], InlineQueryResultCachedVideo)

    async def test_handler_empty_query_returns_early(self):
        """Handler with empty query does nothing"""
        mock_query = AsyncMock()
        mock_query.query = ''
        mock_query.from_user = MagicMock(id=self.test_config.user_id)
        mock_query.answer = AsyncMock()

        update = MagicMock()
        update.inline_query = mock_query

        context = MagicMock()
        context.bot_data = {'state': self.state}
        context.user_data = {}

        await inline_query_handler(update, context)

        mock_query.answer.assert_not_called()

    async def test_handler_uses_cache_on_second_call(self):
        """Second call with same query uses cached results"""
        url = self.test_config.test_video_url
        self.state.animation_file_id = 'test_anim_id'

        # First call — extracts info
        mock_query1 = AsyncMock()
        mock_query1.query = url
        mock_query1.from_user = MagicMock(id=self.test_config.user_id)
        mock_query1.answer = AsyncMock()

        update1 = MagicMock()
        update1.inline_query = mock_query1

        context = MagicMock()
        context.bot_data = {'state': self.state}
        context.user_data = {}

        await inline_query_handler(update1, context)

        # Second call — should use TemporaryInlineQuery cache
        mock_query2 = AsyncMock()
        mock_query2.query = url
        mock_query2.from_user = MagicMock(id=self.test_config.user_id)
        mock_query2.answer = AsyncMock()

        update2 = MagicMock()
        update2.inline_query = mock_query2

        with patch('dasovbot.handlers.inline.extract_info') as mock_extract:
            await inline_query_handler(update2, context)
            mock_extract.assert_not_called()

        mock_query2.answer.assert_called_once()


class TestChosenQuery(IntegrationTestBase):
    """Test chosen_query handler with real bot API"""

    def setUp(self):
        _requires_real_video(self.test_config)

    async def test_chosen_query_with_cached_file(self):
        """chosen_query with file_id calls edit_message_media"""
        url = self.test_config.test_video_url
        info = await extract_info(url, download=False, state=self.state)
        info.file_id = 'cached_file_id'
        self.state.videos[url] = info

        result_id = str(uuid4())
        inline_result = MagicMock()
        inline_result.result_id = result_id
        inline_result.from_user = MagicMock(id=self.test_config.user_id)
        inline_result.inline_message_id = 'test_inline_msg'

        update = MagicMock()
        update.chosen_inline_result = inline_result

        mock_bot = AsyncMock()
        context = MagicMock()
        context.bot_data = {'state': self.state}
        context.user_data = {
            'inline_queries': {result_id: {'url': url, 'upload_date': info.upload_date}},
        }
        context.bot = mock_bot

        await chosen_query(update, context)

        mock_bot.edit_message_media.assert_called_once()

    async def test_chosen_query_without_file_id_creates_intent(self):
        """chosen_query without file_id appends download intent"""
        url = self.test_config.test_video_url
        info = await extract_info(url, download=False, state=self.state)
        info.file_id = None
        self.state.videos[url] = info

        result_id = str(uuid4())

        # Populate temporary_inline_queries so title lookup works
        tiq = TemporaryInlineQuery(timestamp=now())
        mock_result = MagicMock()
        mock_result.id = result_id
        mock_result.title = info.title
        tiq.results = [mock_result]
        self.state.temporary_inline_queries[url] = tiq

        inline_result = MagicMock()
        inline_result.result_id = result_id
        inline_result.from_user = MagicMock(id=self.test_config.user_id)
        inline_result.inline_message_id = 'test_inline_msg'

        update = MagicMock()
        update.chosen_inline_result = inline_result

        context = MagicMock()
        context.bot_data = {'state': self.state}
        context.user_data = {
            'inline_queries': {result_id: {'url': url, 'upload_date': info.upload_date}},
        }
        context.bot = AsyncMock()

        with patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock) as mock_append:
            await chosen_query(update, context)
            mock_append.assert_called_once()
            self.assertEqual(mock_append.call_args.args[0], url)


class TestInlineDownloadAndSend(IntegrationTestBase):
    """Extract info from real URL and send it via real bot API"""

    def setUp(self):
        _requires_real_video(self.test_config)

    async def test_extract_and_send_info_message(self):
        """Extract real video info and send caption to chat"""
        url = self.test_config.test_video_url
        info = await extract_info(url, download=False, state=self.state)

        message = await self.bot.send_message(
            chat_id=self.test_config.chat_id,
            text=info.caption,
        )
        self.track_message(message)

        self.assertIsNotNone(message.text)
        self.assertEqual(message.chat.id, self.test_config.chat_id)

    async def test_extract_and_send_thumbnail(self):
        """Extract real video info and send thumbnail URL if available"""
        url = self.test_config.test_video_url
        info = await extract_info(url, download=False, state=self.state)

        if not info.thumbnail:
            self.skipTest('Video has no thumbnail')

        message = await self.bot.send_photo(
            chat_id=self.test_config.chat_id,
            photo=info.thumbnail,
            caption=info.title[:200],
        )
        self.track_message(message)

        self.assertIsNotNone(message.photo)


class TestVideoDownload(IntegrationTestBase):
    """Real video download via yt-dlp and upload to Telegram.

    These are slow (actual download + upload). Enable with TEST_ENABLE_DOWNLOAD=1.
    """

    def setUp(self):
        _requires_real_video(self.test_config)
        if not os.getenv('TEST_ENABLE_DOWNLOAD'):
            self.skipTest(
                'Download tests disabled. Set TEST_ENABLE_DOWNLOAD=1 to enable'
            )

    async def test_download_and_send_video(self):
        """Download real video and send it to the test chat"""
        url = self.test_config.test_video_url
        info = await extract_info(url, download=True, state=self.state)

        self.assertIsNotNone(info)
        self.assertIsNotNone(info.filepath)
        self.assertTrue(os.path.exists(info.filepath))

        message = await self.bot.send_video(
            chat_id=self.test_config.chat_id,
            video=info.filepath,
            caption=info.caption,
            duration=info.duration,
            width=info.width,
            height=info.height,
        )
        self.track_message(message)

        self.assertIsNotNone(message.video)
        self.assertIsNotNone(message.video.file_id)

        # Clean up downloaded file
        try:
            os.remove(info.filepath)
        except OSError:
            pass


if __name__ == '__main__':
    unittest.main()
