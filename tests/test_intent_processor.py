import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import make_state, make_config
from dasovbot.models import VideoInfo, Intent, IntentMessage
from dasovbot.services.intent_processor import (
    filter_intents, append_intent, post_process, process_intent, process_query,
    retry_lower_quality, file_size_mb,
)


class TestFileSizeMb(unittest.TestCase):
    def test_missing_file_returns_zero(self):
        self.assertEqual(file_size_mb('/no/such/file'), 0)

    @patch('dasovbot.services.intent_processor.os.path.getsize', return_value=3 << 20)
    def test_returns_whole_megabytes(self, mock_getsize):
        self.assertEqual(file_size_mb('/tmp/x'), 3)


class TestFilterIntents(unittest.TestCase):
    def test_filters_ignored(self):
        intents = {
            'a': Intent(ignored=False),
            'b': Intent(ignored=True),
            'c': Intent(ignored=False),
        }
        result = filter_intents(intents)
        self.assertEqual(set(result.keys()), {'a', 'c'})

    def test_empty(self):
        self.assertEqual(filter_intents({}), {})

    def test_all_ignored(self):
        intents = {'a': Intent(ignored=True), 'b': Intent(ignored=True)}
        self.assertEqual(filter_intents(intents), {})

    def test_none_ignored(self):
        intents = {'a': Intent(), 'b': Intent()}
        result = filter_intents(intents)
        self.assertEqual(len(result), 2)


class TestAppendIntent(unittest.IsolatedAsyncioTestCase):
    def _make_state(self, **overrides):
        config = make_config()
        return make_state(config=config, **overrides)

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_creates_new_intent(self, mock_upsert):
        state = self._make_state()
        await append_intent('url1', state, chat_ids=['100'])
        self.assertIn('url1', state.intents)
        self.assertEqual(state.intents['url1'].chat_ids, ['100'])

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_appends_to_existing(self, mock_upsert):
        intent = Intent(chat_ids=['100'])
        state = self._make_state(intents={'url1': intent})
        await append_intent('url1', state, chat_ids=['200'])
        self.assertEqual(state.intents['url1'].chat_ids, ['100', '200'])

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_deduplicates_chat_ids(self, mock_upsert):
        intent = Intent(chat_ids=['100'])
        state = self._make_state(intents={'url1': intent})
        await append_intent('url1', state, chat_ids=['100'])
        self.assertEqual(state.intents['url1'].chat_ids, ['100'])

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_appends_inline_message_id(self, mock_upsert):
        state = self._make_state()
        await append_intent('url1', state, inline_message_id='imid1')
        self.assertEqual(state.intents['url1'].inline_message_ids, ['imid1'])

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_appends_message(self, mock_upsert):
        state = self._make_state()
        await append_intent('url1', state, message={'chat': 'c1', 'message': 'm1'})
        self.assertEqual(len(state.intents['url1'].messages), 1)
        self.assertEqual(state.intents['url1'].messages[0].chat, 'c1')

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_sets_source_title_upload_date(self, mock_upsert):
        state = self._make_state()
        await append_intent('url1', state, source='download', title='My Video', upload_date='20240101')
        intent = state.intents['url1']
        self.assertEqual(intent.source, 'download')
        self.assertEqual(intent.title, 'My Video')
        self.assertEqual(intent.upload_date, '20240101')

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_wont_overwrite_existing_source(self, mock_upsert):
        intent = Intent(source='subscription')
        state = self._make_state(intents={'url1': intent})
        await append_intent('url1', state, source='download')
        self.assertEqual(state.intents['url1'].source, 'subscription')

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_ignored_intent_no_priority_bump(self, mock_upsert):
        intent = Intent(ignored=True, priority=0)
        state = self._make_state(intents={'url1': intent})
        await append_intent('url1', state, chat_ids=['100'])
        self.assertEqual(state.intents['url1'].priority, 0)

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_signals_queue(self, mock_upsert):
        state = self._make_state()
        await append_intent('url1', state, chat_ids=['100'])
        self.assertFalse(state.download_queue.empty())
        self.assertEqual(state.download_queue.get_nowait(), 'url1')

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_priority_increases_by_chat_ids_count(self, mock_upsert):
        state = self._make_state()
        await append_intent('url1', state, chat_ids=['1', '2', '3'])
        self.assertEqual(state.intents['url1'].priority, 3)

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_priority_increases_by_2_when_no_chat_ids(self, mock_upsert):
        state = self._make_state()
        await append_intent('url1', state)
        self.assertEqual(state.intents['url1'].priority, 2)


class TestProcessIntent(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_sends_to_chat_ids(self, mock_delete):
        bot = AsyncMock()
        intent = Intent(chat_ids=['10', '20'])
        state = make_state(intents={'q': intent})
        result = await process_intent(bot, 'q', 'file123', 'caption', state)
        self.assertEqual(bot.send_video.await_count, 2)
        bot.send_video.assert_any_await(chat_id='10', video='file123', caption='caption', disable_notification=True)
        bot.send_video.assert_any_await(chat_id='20', video='file123', caption='caption', disable_notification=True)
        self.assertIs(result, intent)

    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_edits_inline_message_ids(self, mock_delete):
        bot = AsyncMock()
        intent = Intent(inline_message_ids=['im1', 'im2'])
        state = make_state(intents={'q': intent})
        await process_intent(bot, 'q', 'file123', 'caption', state)
        self.assertEqual(bot.edit_message_media.await_count, 2)

    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_edits_messages(self, mock_delete):
        bot = AsyncMock()
        intent = Intent(messages=[IntentMessage(chat='c1', message='m1')])
        state = make_state(intents={'q': intent})
        await process_intent(bot, 'q', 'file123', 'caption', state)
        bot.edit_message_media.assert_awaited_once()

    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_returns_none_when_no_intent(self, mock_delete):
        bot = AsyncMock()
        state = make_state()
        result = await process_intent(bot, 'missing', 'file123', 'caption', state)
        self.assertIsNone(result)

    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_pops_intent(self, mock_delete):
        bot = AsyncMock()
        intent = Intent(chat_ids=['10'])
        state = make_state(intents={'q': intent})
        await process_intent(bot, 'q', 'file123', 'caption', state)
        self.assertNotIn('q', state.intents)

    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_error_on_one_continues_to_next(self, mock_delete):
        bot = AsyncMock()
        bot.send_video.side_effect = [Exception('fail'), AsyncMock()]
        intent = Intent(chat_ids=['10', '20'])
        state = make_state(intents={'q': intent})
        result = await process_intent(bot, 'q', 'file123', 'caption', state)
        self.assertEqual(bot.send_video.await_count, 2)
        self.assertIs(result, intent)

    @patch('dasovbot.services.intent_processor.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_retries_on_rate_limit(self, mock_delete, mock_sleep):
        from telegram.error import RetryAfter
        bot = AsyncMock()
        bot.send_video.side_effect = [RetryAfter(3), AsyncMock()]
        intent = Intent(chat_ids=['10'])
        state = make_state(intents={'q': intent})
        await process_intent(bot, 'q', 'file123', 'caption', state)
        # Throttled once, then delivered on the retry rather than being dropped.
        self.assertEqual(bot.send_video.await_count, 2)
        mock_sleep.assert_awaited_once_with(3)

    @patch('dasovbot.services.intent_processor.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_gives_up_after_max_send_retries(self, mock_delete, mock_sleep):
        from telegram.error import RetryAfter
        from dasovbot.constants import MAX_SEND_RETRIES
        bot = AsyncMock()
        bot.send_video.side_effect = RetryAfter(1)
        intent = Intent(chat_ids=['10'])
        state = make_state(intents={'q': intent})
        await process_intent(bot, 'q', 'file123', 'caption', state)
        self.assertEqual(bot.send_video.await_count, MAX_SEND_RETRIES + 1)


class TestPostProcess(unittest.IsolatedAsyncioTestCase):
    def _make_message(self, file_id='fid1'):
        message = AsyncMock()
        message.video = MagicMock()
        message.video.file_id = file_id
        message.chat_id = '999'
        return message

    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_extracts_file_id(self, mock_upsert):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        msg = self._make_message('fid99')
        result = await post_process('q', info, msg, state)
        self.assertEqual(result, 'fid99')
        self.assertEqual(info.file_id, 'fid99')

    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_stores_video(self, mock_upsert):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        msg = self._make_message()
        await post_process('q', info, msg, state)
        self.assertIn('q', state.videos)
        self.assertIn('https://example.com', state.videos)

    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_deletes_message(self, mock_upsert):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        msg = self._make_message()
        await post_process('q', info, msg, state)
        msg.delete.assert_awaited_once()

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_removes_filepath(self, mock_upsert, mock_remove):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath='/tmp/media/video.mp4')
        msg = self._make_message()
        await post_process('q', info, msg, state)
        mock_remove.assert_called_once_with('/tmp/media/video.mp4')

    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_store_info_false_skips_db(self, mock_upsert):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        msg = self._make_message()
        await post_process('q', info, msg, state, store_info=False)
        mock_upsert.assert_not_awaited()
        self.assertNotIn('q', state.videos)

    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_sets_origin(self, mock_upsert):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        origin_info = VideoInfo(title='O', width=1920, height=1080, format='mp4')
        msg = self._make_message()
        await post_process('q', info, msg, state, origin_info=origin_info)
        self.assertIsNotNone(info.origin)
        self.assertEqual(info.origin.width, 1920)
        self.assertEqual(info.origin.height, 1080)

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.services.intent_processor.shutil.move')
    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_developer_export_moves_file(self, mock_upsert_video, mock_upsert_intent, mock_move, mock_remove):
        intent = Intent(chat_ids=['123'])
        state = make_state(config=make_config(developer_id='123'), intents={'q': intent})
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath='/tmp/media/v.mp4')
        msg = self._make_message()
        await post_process('q', info, msg, state)
        mock_move.assert_called_once_with('/tmp/media/v.mp4', '/tmp/export/v.mp4')
        mock_remove.assert_not_called()

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.services.intent_processor.shutil.move', side_effect=OSError('boom'))
    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_developer_export_move_error_removes_file(self, mock_upsert_video, mock_upsert_intent, mock_move, mock_remove):
        intent = Intent(chat_ids=['123'])
        state = make_state(config=make_config(developer_id='123'), intents={'q': intent})
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath='/tmp/media/v.mp4')
        msg = self._make_message()
        await post_process('q', info, msg, state)
        mock_remove.assert_called_once_with('/tmp/media/v.mp4')

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.services.intent_processor.shutil.move')
    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_developer_export_skips_path_outside_media(self, mock_upsert_video, mock_upsert_intent, mock_move, mock_remove):
        intent = Intent(chat_ids=['123'])
        state = make_state(config=make_config(developer_id='123'), intents={'q': intent})
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath='/tmp/v.mp4')
        msg = self._make_message()
        await post_process('q', info, msg, state)
        mock_move.assert_not_called()
        mock_remove.assert_called_once_with('/tmp/v.mp4')

    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_falls_back_to_document_file_id(self, mock_upsert):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        msg = self._make_message()
        msg.video = None
        msg.document = MagicMock()
        msg.document.file_id = 'doc1'
        result = await post_process('q', info, msg, state)
        self.assertEqual(result, 'doc1')
        self.assertIn('q', state.videos)

    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_no_attachment_skips_store_and_returns_none(self, mock_upsert, mock_remove):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath='/tmp/media/v.mp4')
        msg = self._make_message()
        msg.video = None
        msg.document = None
        msg.animation = None
        result = await post_process('q', info, msg, state)
        self.assertIsNone(result)
        self.assertNotIn('q', state.videos)
        mock_remove.assert_called_once_with('/tmp/media/v.mp4')

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_sets_source_from_intent(self, mock_upsert_video, mock_upsert_intent):
        intent = Intent(source='subscription')
        state = make_state(config=make_config(), intents={'q': intent})
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        msg = self._make_message()
        await post_process('q', info, msg, state)
        self.assertEqual(info.source, 'subscription')


class TestPostProcessDeleteError(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_delete_failure_still_returns_file_id(self, mock_upsert):
        state = make_state(config=make_config())
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        message = AsyncMock()
        message.video = MagicMock()
        message.video.file_id = 'fid1'
        message.chat_id = '999'
        message.delete.side_effect = Exception('already deleted')
        result = await post_process('q', info, message, state)
        self.assertEqual(result, 'fid1')


class TestProcessQuery(unittest.IsolatedAsyncioTestCase):
    def _make_info(self, **overrides):
        defaults = dict(
            title='T', webpage_url='https://example.com/v', caption='caption',
            filepath='/tmp/media/video.mp4', filename='video.mp4',
            duration=10, width=640, height=360,
        )
        defaults.update(overrides)
        return VideoInfo(**defaults)

    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.post_process', new_callable=AsyncMock, return_value='fid1')
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_uploads_by_filesystem_path(self, mock_extract, mock_post, mock_process_intent):
        # PTB local mode only applies to str/Path arguments: passing bytes or
        # an open file object silently falls back to reading the whole video
        # into memory, which is what OOM'd the host
        mock_extract.return_value = self._make_info()
        state = make_state(config=make_config())
        bot = AsyncMock()

        await process_query(bot, 'q', state)

        bot.send_video.assert_awaited_once()
        video_arg = bot.send_video.await_args.kwargs['video']
        self.assertIsInstance(video_arg, str)
        self.assertEqual(video_arg, '/tmp/media/video.mp4')

    @patch('dasovbot.services.intent_processor.drop_or_retry_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_no_video_path_and_no_url_drops_instead_of_crashing(self, mock_extract, mock_drop):
        # Dead videos can come back with neither webpage_url nor url: the
        # youtube check must not TypeError on None, or the worker crash-loops
        # on the same intent forever
        mock_extract.return_value = self._make_info(webpage_url=None, filepath=None)
        state = make_state(config=make_config())
        bot = AsyncMock()

        await process_query(bot, 'q', state)

        mock_drop.assert_awaited_once()
        bot.send_video.assert_not_awaited()

    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_cached_file_id_skips_upload(self, mock_extract, mock_process_intent):
        mock_extract.return_value = self._make_info(file_id='cached', filepath=None)
        state = make_state(config=make_config())
        bot = AsyncMock()

        await process_query(bot, 'q', state)

        bot.send_video.assert_not_awaited()
        mock_process_intent.assert_awaited_once_with(bot, 'q', 'cached', 'caption', state)

    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.post_process', new_callable=AsyncMock, return_value='fid1')
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_upload_waits_for_semaphore(self, mock_extract, mock_post, mock_process_intent):
        mock_extract.return_value = self._make_info()
        state = make_state(config=make_config())
        bot = AsyncMock()

        await state.upload_semaphore.acquire()
        task = asyncio.create_task(process_query(bot, 'q', state))
        try:
            for _ in range(5):
                await asyncio.sleep(0)
            bot.send_video.assert_not_awaited()
        finally:
            state.upload_semaphore.release()
        await task
        bot.send_video.assert_awaited_once()

    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.post_process', new_callable=AsyncMock, return_value='fid1')
    @patch('dasovbot.services.intent_processor.extract_info', new_callable=AsyncMock)
    async def test_concurrent_uploads_never_overlap(self, mock_extract, mock_post, mock_process_intent):
        mock_extract.side_effect = lambda query, **kwargs: self._make_info(
            webpage_url=f'https://example.com/{query}', filepath=f'/tmp/media/{query}.mp4')
        state = make_state(config=make_config())

        active = 0
        seen = []

        async def fake_send(*args, **kwargs):
            nonlocal active
            active += 1
            seen.append(active)
            await asyncio.sleep(0.01)
            active -= 1
            return MagicMock()

        bot = AsyncMock()
        bot.send_video = AsyncMock(side_effect=fake_send)

        await asyncio.gather(process_query(bot, 'a', state), process_query(bot, 'b', state))

        self.assertEqual(bot.send_video.await_count, 2)
        self.assertEqual(max(seen), 1)


class TestRetryLowerQuality(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.intent_processor.remove')
    @patch('dasovbot.services.intent_processor.process_intent', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.post_process', new_callable=AsyncMock, return_value='fid1')
    @patch('dasovbot.services.intent_processor.process_info')
    @patch('dasovbot.services.intent_processor.yt_dlp.YoutubeDL')
    async def test_fallback_upload_waits_for_semaphore_and_sends_path(
            self, mock_ydl_cls, mock_process_info, mock_post, mock_process_intent, mock_remove):
        mock_ydl_cls.return_value.__enter__.return_value = MagicMock()
        mock_process_info.return_value = VideoInfo(
            title='T', webpage_url='https://example.com/v', caption='caption',
            filepath='/tmp/media/video.scaled.mp4', width=320, height=180,
        )
        info = VideoInfo(
            title='T', webpage_url='https://example.com/v', caption='caption',
            filepath='/tmp/media/video.mp4', filename='video.mp4',
            duration=10, width=640, height=360,
        )
        state = make_state(config=make_config())
        bot = AsyncMock()

        # The retry re-uploads a fresh file: it must queue behind other
        # uploads instead of piling a second send on top of a stuck one
        await state.upload_semaphore.acquire()
        task = asyncio.create_task(retry_lower_quality(bot, 'q', info, state))
        try:
            for _ in range(10):
                await asyncio.sleep(0)
            bot.send_video.assert_not_awaited()
        finally:
            state.upload_semaphore.release()
        result = await task

        self.assertTrue(result)
        video_arg = bot.send_video.await_args.kwargs['video']
        self.assertIsInstance(video_arg, str)
        self.assertEqual(video_arg, '/tmp/media/video.scaled.mp4')
        mock_remove.assert_called_with('/tmp/media/video.scaled.mp4')


class TestProcessIntentEditErrors(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_inline_edit_error_continues(self, mock_delete):
        intent = Intent(inline_message_ids=['im1', 'im2'])
        state = make_state(intents={'q': intent})
        bot = AsyncMock()
        bot.edit_message_media.side_effect = Exception('gone')
        result = await process_intent(bot, 'q', 'fid', 'caption', state)
        self.assertIs(result, intent)
        self.assertEqual(bot.edit_message_media.await_count, 2)

    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_message_edit_error_continues(self, mock_delete):
        intent = Intent(messages=[IntentMessage(chat='1', message='10'),
                                  IntentMessage(chat='2', message='20')])
        state = make_state(intents={'q': intent})
        bot = AsyncMock()
        bot.edit_message_media.side_effect = Exception('gone')
        result = await process_intent(bot, 'q', 'fid', 'caption', state)
        self.assertIs(result, intent)
        self.assertEqual(bot.edit_message_media.await_count, 2)


if __name__ == '__main__':
    unittest.main()
