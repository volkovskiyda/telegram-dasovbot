import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import NetworkError

from dasovbot.constants import MAX_INTENT_RETRIES
from dasovbot.models import VideoInfo, Intent
from dasovbot.services.intent_processor import process_query, retry_lower_quality, process_intents
from tests.helpers import make_state, make_config


def make_video_info(**overrides):
    defaults = dict(
        title='title', caption='caption', webpage_url='https://example.com/v',
        filepath='/media/video.webm', filename='video.webm',
        duration=10, width=640, height=360,
    )
    defaults.update(overrides)
    return VideoInfo(**defaults)


class TestProcessQuery(unittest.IsolatedAsyncioTestCase):
    def _make_state(self, **overrides):
        return make_state(config=make_config(), **overrides)

    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock, return_value=None)
    async def test_no_info_retries_before_dropping(self, mock_extract):
        intent = Intent(chat_ids=['1'])
        state = self._make_state(intents={'q': intent})
        result = await process_query(AsyncMock(), 'q', state)
        self.assertIsNone(result)
        self.assertIn('q', state.intents)
        self.assertEqual(intent.retries, 1)

    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock, return_value=None)
    async def test_no_info_drops_after_max_retries(self, mock_extract):
        state = self._make_state(intents={'q': Intent(chat_ids=['1'], retries=MAX_INTENT_RETRIES - 1)})
        await process_query(AsyncMock(), 'q', state)
        self.assertNotIn('q', state.intents)

    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock, return_value=None)
    async def test_no_info_keeps_ignored_intent(self, mock_extract):
        intent = Intent(ignored=True)
        state = self._make_state(intents={'q': intent})
        await process_query(AsyncMock(), 'q', state)
        self.assertIn('q', state.intents)

    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_cached_file_id_skips_upload(self, mock_extract, mock_process_intent):
        info = make_video_info(file_id='fid', filepath=None)
        mock_extract.return_value = info
        state = self._make_state(intents={'q': Intent(chat_ids=['1'])})
        bot = AsyncMock()
        result = await process_query(bot, 'q', state)
        self.assertIs(result, info)
        bot.send_video.assert_not_awaited()
        mock_process_intent.assert_awaited_once_with(bot, 'q', 'fid', 'caption', state)

    @patch('dasovbot.services.intent_processor.send_message_developer', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_no_filepath_notifies_developer_and_pops(self, mock_extract, mock_send_dev):
        info = make_video_info(filepath=None, webpage_url='https://www.youtube.com/watch?v=1')
        mock_extract.return_value = info
        state = self._make_state(intents={'q': Intent(chat_ids=['1'])})
        bot = AsyncMock()
        await process_query(bot, 'q', state)
        mock_send_dev.assert_awaited_once()
        bot.send_video.assert_not_awaited()
        self.assertNotIn('q', state.intents)

    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.post_process', new_callable=AsyncMock, return_value='fid2')
    @patch('dasovbot.services.intent_processor.convert_to_mp4', new_callable=AsyncMock, return_value='/media/video.mp4')
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_downloads_converts_and_posts(self, mock_extract, mock_convert, mock_post, mock_process_intent):
        info = make_video_info()
        mock_extract.return_value = info
        state = self._make_state(intents={'q': Intent(chat_ids=['1'])})
        bot = AsyncMock()
        await process_query(bot, 'q', state)
        bot.send_video.assert_awaited_once()
        self.assertEqual(bot.send_video.await_args.kwargs['video'], '/media/video.mp4')
        self.assertEqual(info.filepath, '/media/video.mp4')
        self.assertEqual(info.filename, 'video.mp4')
        mock_process_intent.assert_awaited_once_with(bot, 'q', 'fid2', 'caption', state)

    @patch('dasovbot.services.intent_processor.retry_lower_quality', new_callable=AsyncMock, return_value=True)
    @patch('dasovbot.services.intent_processor.os.path.getsize', return_value=3 * 1024 ** 3)
    @patch('dasovbot.services.intent_processor.convert_to_mp4', new_callable=AsyncMock, return_value='/media/video.mp4')
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_network_error_large_youtube_retries_lower_quality(
            self, mock_extract, mock_convert, mock_getsize, mock_retry):
        info = make_video_info(webpage_url='https://www.youtube.com/watch?v=1')
        mock_extract.return_value = info
        state = self._make_state(intents={'q': Intent(chat_ids=['1'])})
        bot = AsyncMock()
        bot.send_video.side_effect = NetworkError('Request Entity Too Large')
        result = await process_query(bot, 'q', state)
        mock_retry.assert_awaited_once()
        self.assertIs(result, info)
        # retry_lower_quality handles the intent; process_query must not pop it
        self.assertIn('q', state.intents)

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.services.intent_processor.convert_to_mp4', new_callable=AsyncMock, return_value='/media/video.mp4')
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_send_error_removes_file_and_retries(self, mock_extract, mock_convert, mock_remove):
        info = make_video_info()
        mock_extract.return_value = info
        intent = Intent(chat_ids=['1'])
        state = self._make_state(intents={'q': intent})
        bot = AsyncMock()
        bot.send_video.side_effect = ValueError('boom')
        await process_query(bot, 'q', state)
        mock_remove.assert_called_once_with(info.filepath)
        self.assertIn('q', state.intents)
        self.assertEqual(intent.retries, 1)

    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.post_process', new_callable=AsyncMock, return_value=None)
    @patch('dasovbot.services.intent_processor.convert_to_mp4', new_callable=AsyncMock, return_value='/media/video.mp4')
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_post_process_failure_retries_intent(self, mock_extract, mock_convert, mock_post, mock_process_intent):
        info = make_video_info()
        mock_extract.return_value = info
        intent = Intent(chat_ids=['1'])
        state = self._make_state(intents={'q': intent})
        bot = AsyncMock()
        result = await process_query(bot, 'q', state)
        self.assertIs(result, info)
        mock_process_intent.assert_not_awaited()
        self.assertIn('q', state.intents)
        self.assertEqual(intent.retries, 1)

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.services.intent_processor.convert_to_mp4', new_callable=AsyncMock, return_value='/media/video.mp4')
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_send_error_drops_after_max_retries(self, mock_extract, mock_convert, mock_remove):
        info = make_video_info()
        mock_extract.return_value = info
        state = self._make_state(intents={'q': Intent(chat_ids=['1'], retries=MAX_INTENT_RETRIES - 1)})
        bot = AsyncMock()
        bot.send_video.side_effect = ValueError('boom')
        await process_query(bot, 'q', state)
        self.assertNotIn('q', state.intents)


class TestRetryLowerQuality(unittest.IsolatedAsyncioTestCase):
    def _make_state(self, **overrides):
        return make_state(config=make_config(), **overrides)

    @patch('dasovbot.services.intent_processor.send_message_developer', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.yt_dlp.YoutubeDL')
    async def test_fallback_download_error_returns_false(self, mock_ydl_cls, mock_send_dev):
        mock_ydl_cls.return_value.__enter__.return_value.extract_info.side_effect = ValueError('boom')
        state = self._make_state()
        result = await retry_lower_quality(AsyncMock(), 'q', make_video_info(), state)
        self.assertFalse(result)

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.post_process', new_callable=AsyncMock, return_value='fid')
    @patch('dasovbot.services.intent_processor.convert_to_mp4', new_callable=AsyncMock, return_value='/media/video.scaled.mp4')
    @patch('dasovbot.services.intent_processor.send_message_developer', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.yt_dlp.YoutubeDL')
    async def test_success_posts_and_cleans_up(
            self, mock_ydl_cls, mock_send_dev, mock_convert, mock_post, mock_process_intent, mock_remove):
        mock_ydl_cls.return_value.__enter__.return_value.extract_info.return_value = {
            'webpage_url': 'https://www.youtube.com/watch?v=1',
            'title': 'title',
            'width': 640,
            'height': 360,
            'requested_downloads': [
                {'filepath': '/media/video.scaled.webm', 'filename': 'video.scaled.webm'},
            ],
        }
        state = self._make_state()
        bot = AsyncMock()
        result = await retry_lower_quality(bot, 'q', make_video_info(), state)
        self.assertTrue(result)
        bot.send_video.assert_awaited_once()
        mock_process_intent.assert_awaited_once_with(bot, 'q', 'fid', 'caption', state)
        mock_remove.assert_called_once_with('/media/video.scaled.mp4')

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.services.intent_processor.convert_to_mp4', new_callable=AsyncMock, return_value='/media/video.scaled.mp4')
    @patch('dasovbot.services.intent_processor.send_message_developer', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.yt_dlp.YoutubeDL')
    async def test_send_failure_returns_false_and_cleans(
            self, mock_ydl_cls, mock_send_dev, mock_convert, mock_remove):
        mock_ydl_cls.return_value.__enter__.return_value.extract_info.return_value = {
            'webpage_url': 'https://www.youtube.com/watch?v=1',
            'title': 'title',
            'requested_downloads': [
                {'filepath': '/media/video.scaled.webm', 'filename': 'video.scaled.webm'},
            ],
        }
        state = self._make_state()
        bot = AsyncMock()
        bot.send_video.side_effect = ValueError('boom')
        result = await retry_lower_quality(bot, 'q', make_video_info(), state)
        self.assertFalse(result)
        mock_remove.assert_called_once_with('/media/video.scaled.mp4')


class TestProcessIntents(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.intent_processor.process_query', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.asyncio.sleep', new_callable=AsyncMock)
    async def test_drains_queue_and_processes_highest_priority(self, mock_sleep, mock_process_query):
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        state = make_state(config=make_config(), intents={
            'low': Intent(priority=1),
            'high': Intent(priority=5),
            'skipped': Intent(priority=99, ignored=True),
        })
        for _ in range(3):
            state.download_queue.put_nowait('low')
        bot = AsyncMock()
        with self.assertRaises(asyncio.CancelledError):
            await process_intents(bot, state)
        mock_process_query.assert_awaited_once_with(bot, 'high', state)
        self.assertTrue(state.download_queue.empty())
        self.assertIn('monitor_process_intents', state.background_task_status)


if __name__ == '__main__':
    unittest.main()
