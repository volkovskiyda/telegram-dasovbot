import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import make_state, make_config
from dasovbot.models import VideoInfo, Intent, IntentMessage
from dasovbot.services.intent_processor import (
    filter_intents, append_intent, post_process, process_intent,
)


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

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_sets_source_from_intent(self, mock_upsert_video, mock_upsert_intent):
        intent = Intent(source='subscription')
        state = make_state(config=make_config(), intents={'q': intent})
        info = VideoInfo(title='T', webpage_url='https://example.com', filepath=None)
        msg = self._make_message()
        await post_process('q', info, msg, state)
        self.assertEqual(info.source, 'subscription')


if __name__ == '__main__':
    unittest.main()
