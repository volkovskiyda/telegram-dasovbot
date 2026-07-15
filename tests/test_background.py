import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dasovbot.models import Subscription, TemporaryInlineQuery, VideoInfo
from dasovbot.services.background import (
    populate_animation, populate_video, populate_playlist, run_populate_subscriptions,
    clear_temporary_inline_queries,
)
from tests.helpers import make_state, make_config


def make_video_info(**overrides):
    defaults = dict(
        title='title', filepath='/media/video.mp4', filename='video.mp4',
        duration=10, width=640, height=360, caption='caption',
    )
    defaults.update(overrides)
    return VideoInfo(**defaults)


class TestPopulateAnimation(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_skips_when_already_set(self, mock_extract):
        state = make_state(animation_file_id='anim123')
        await populate_animation(AsyncMock(), state)
        mock_extract.assert_not_awaited()

    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_skips_when_no_loading_video_id(self, mock_extract):
        state = make_state(animation_file_id=None, config=make_config(loading_video_id=''))
        await populate_animation(AsyncMock(), state)
        mock_extract.assert_not_awaited()

    @patch('dasovbot.services.background.post_process', new_callable=AsyncMock, return_value='file123')
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_success_sets_animation_file_id(self, mock_extract, mock_post):
        mock_extract.return_value = make_video_info()
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        await populate_animation(bot, state)
        bot.send_video.assert_awaited_once()
        self.assertEqual(state.animation_file_id, 'file123')

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.post_process', new_callable=AsyncMock, return_value='file123')
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_retries_after_failed_extraction(self, mock_extract, mock_post, mock_sleep):
        mock_extract.side_effect = [None, make_video_info()]
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        await populate_animation(bot, state)
        self.assertEqual(mock_extract.await_count, 2)
        self.assertEqual(state.animation_file_id, 'file123')

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock, return_value=None)
    async def test_gives_up_without_crashing(self, mock_extract, mock_sleep):
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        await populate_animation(bot, state)
        self.assertEqual(mock_extract.await_count, 5)
        self.assertIsNone(state.animation_file_id)
        bot.send_video.assert_not_awaited()

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_send_video_error_is_retried(self, mock_extract, mock_sleep):
        mock_extract.return_value = make_video_info()
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        bot.send_video.side_effect = Exception('network error')
        await populate_animation(bot, state)
        self.assertEqual(bot.send_video.await_count, 5)
        self.assertIsNone(state.animation_file_id)


class TestPopulateVideo(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.append_intent', new_callable=AsyncMock)
    async def test_returns_cached_video(self, mock_append):
        info = make_video_info(file_id='cached')
        state = make_state(videos={'url1': info})
        result = await populate_video('url1', ['100'], state)
        self.assertIs(result, info)
        mock_append.assert_not_awaited()

    @patch('dasovbot.services.background.append_intent', new_callable=AsyncMock)
    async def test_appends_intent_when_not_cached(self, mock_append):
        state = make_state(videos={})
        await populate_video('url1', ['100'], state, title='t', upload_date='20260101')
        mock_append.assert_awaited_once()
        self.assertEqual(mock_append.call_args[0][0], 'url1')
        self.assertEqual(mock_append.call_args[1]['chat_ids'], ['100'])


class TestPopulatePlaylist(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.populate_video', new_callable=AsyncMock)
    @patch('dasovbot.services.background.get_ydl')
    async def test_extraction_error_returns(self, mock_get_ydl, mock_populate):
        ydl = MagicMock()
        ydl.extract_info.side_effect = Exception('network error')
        mock_get_ydl.return_value = ydl
        await populate_playlist('channel', ['100'], make_state())
        mock_populate.assert_not_awaited()

    @patch('dasovbot.services.background.populate_video', new_callable=AsyncMock)
    @patch('dasovbot.services.background.get_ydl')
    async def test_no_entries_returns(self, mock_get_ydl, mock_populate):
        ydl = MagicMock()
        ydl.extract_info.return_value = {'entries': []}
        mock_get_ydl.return_value = ydl
        await populate_playlist('channel', ['100'], make_state())
        mock_populate.assert_not_awaited()

    @patch('dasovbot.services.background.populate_video', new_callable=AsyncMock)
    @patch('dasovbot.services.background.get_ydl')
    async def test_populates_latest_entries_oldest_first(self, mock_get_ydl, mock_populate):
        entries = [
            {'url': f'https://example.com/v{i}', 'title': f't{i}', 'duration': 10, 'upload_date': '20260101'}
            for i in range(7)
        ]
        ydl = MagicMock()
        ydl.extract_info.return_value = {'entries': entries}
        mock_get_ydl.return_value = ydl
        await populate_playlist('channel', ['100'], make_state())
        self.assertEqual(mock_populate.await_count, 5)
        first_populated = mock_populate.await_args_list[0][0][0]
        self.assertEqual(first_populated, 'https://example.com/v4')


class TestRunPopulateSubscriptions(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.populate_playlist', new_callable=AsyncMock)
    async def test_populates_subscribed_and_pops_empty(self, mock_populate):
        with_subs = Subscription(chat_ids=['100'], title='a', uploader='u', uploader_videos='url1/videos')
        without_subs = Subscription(chat_ids=[], title='b', uploader='u', uploader_videos='url2/videos')
        state = make_state(subscriptions={'url1': with_subs, 'url2': without_subs})
        state.pop_subscription = AsyncMock()
        await run_populate_subscriptions(state)
        mock_populate.assert_awaited_once_with('url1', ['100'], state)
        state.pop_subscription.assert_awaited_once_with('url2')
        self.assertIn('populate_subscriptions', state.background_task_status)


class TestClearTemporaryInlineQueries(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    async def test_marks_then_deletes_on_second_pass(self, mock_sleep):
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        fresh = TemporaryInlineQuery(marked=False)
        stale = TemporaryInlineQuery(marked=True)
        state = make_state(temporary_inline_queries={'fresh': fresh, 'stale': stale})
        with self.assertRaises(asyncio.CancelledError):
            await clear_temporary_inline_queries(state)
        # After two passes: 'stale' deleted in the first, 'fresh' marked then deleted in the second
        self.assertEqual(state.temporary_inline_queries, {})
        self.assertIn('clear_temporary_inline_queries', state.background_task_status)


if __name__ == '__main__':
    unittest.main()
