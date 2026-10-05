import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationHandlerStop

from dasovbot.handlers.ban import guard_banned
from dasovbot.models import VideoInfo, TemporaryInlineQuery
from dasovbot.services.ban import schedule_fake_failure, fake_download
from tests.helpers import (
    make_user, make_message, make_inline_query, make_chosen_inline_result,
    make_callback_query, make_update, make_context, make_state,
)


class TestScheduleFakeFailure(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.ban.random.uniform', return_value=0)
    async def test_edits_inline_placeholder(self, _):
        bot = AsyncMock()
        state = make_state()

        schedule_fake_failure(bot, state, 'https://example.com/v', inline_message_id='imid')
        self.assertEqual(len(state.background_tasks), 1)
        await asyncio.gather(*state.background_tasks)

        bot.edit_message_caption.assert_awaited_once_with(
            caption='❌ Video unavailable\nhttps://example.com/v', inline_message_id='imid')
        self.assertEqual(state.background_tasks, set())

    @patch('dasovbot.services.ban.random.uniform', return_value=0)
    async def test_edits_chat_placeholder_and_swallows_errors(self, _):
        bot = AsyncMock()
        bot.edit_message_caption.side_effect = Exception('message gone')
        state = make_state()

        schedule_fake_failure(bot, state, 'q', chat_id='1', message_id=2)
        await asyncio.gather(*state.background_tasks)

        bot.edit_message_caption.assert_awaited_once_with(
            caption='❌ Video unavailable\nq', chat_id='1', message_id=2)

    @patch('dasovbot.services.ban.asyncio.sleep', new_callable=AsyncMock)
    async def test_waits_a_random_delay(self, mock_sleep):
        state = make_state()
        schedule_fake_failure(AsyncMock(), state, 'q', inline_message_id='imid')
        await asyncio.gather(*state.background_tasks)

        delay = mock_sleep.await_args.args[0]
        self.assertGreaterEqual(delay, 10)
        self.assertLessEqual(delay, 60)


class TestGuardBanned(unittest.IsolatedAsyncioTestCase):
    def _context(self, **state_kwargs):
        state = make_state(banned_users={'123': {}}, **state_kwargs)
        return make_context(state=state), state

    def _update(self, user_id=123, **kwargs):
        update = make_update(**kwargs)
        update.effective_user = make_user(id=user_id)
        return update

    async def test_other_users_pass_through(self):
        context, state = self._context()
        iq = make_inline_query(query='https://example.com/v1', from_user=make_user(id=7))
        self.assertIsNone(await guard_banned(self._update(user_id=7, inline_query=iq), context))
        self.assertEqual(state.user_requests, {})

    async def test_inline_query_left_unanswered(self):
        context, state = self._context(videos={'https://example.com/v1': VideoInfo(title='T', file_id='f1')})
        iq = make_inline_query(query='https://example.com/v1', from_user=make_user(id=123))
        with self.assertRaises(ApplicationHandlerStop):
            await guard_banned(self._update(inline_query=iq), context)
        iq.answer.assert_not_awaited()
        self.assertEqual(state.temporary_inline_queries, {})

    @patch('dasovbot.handlers.ban.schedule_fake_failure')
    async def test_chosen_result_fails_placeholder(self, mock_fail):
        context, state = self._context(videos={'https://example.com/v1': VideoInfo(title='T', file_id='f1')})
        context.user_data['inline_queries'] = {'rid1': {'url': 'https://example.com/v1', 'upload_date': None}}
        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1', from_user=make_user(id=123))
        with self.assertRaises(ApplicationHandlerStop):
            await guard_banned(self._update(chosen_inline_result=result), context)
        mock_fail.assert_called_once_with(context.bot, state, 'https://example.com/v1', inline_message_id='imid1')
        context.bot.edit_message_media.assert_not_awaited()
        self.assertEqual(state.video_requesters, {'https://example.com/v1': ['123']})
        self.assertNotIn('inline_queries', context.user_data)

    @patch('dasovbot.handlers.ban.schedule_fake_failure')
    async def test_chosen_result_from_an_earlier_query_is_resolved_from_state(self, mock_fail):
        # user_data only holds the latest query; older ids live in state
        tiq = TemporaryInlineQuery(timestamp='t', inline_queries={'rid0': 'https://example.com/v0'})
        context, state = self._context(temporary_inline_queries={'q0': tiq})
        result = make_chosen_inline_result(result_id='rid0', inline_message_id='imid0', from_user=make_user(id=123))
        with self.assertRaises(ApplicationHandlerStop):
            await guard_banned(self._update(chosen_inline_result=result), context)
        mock_fail.assert_called_once_with(context.bot, state, 'https://example.com/v0', inline_message_id='imid0')

    async def test_messages_and_buttons_pass_through(self):
        # Conversation paths (download, subscriptions) check the ban themselves
        context, state = self._context(animation_file_id='anim123')
        for update in [
            self._update(message=make_message(chat_id=123, text='/start')),
            self._update(message=make_message(chat_id=123, text='/subscribe https://example.com/c')),
            self._update(message=make_message(chat_id=123, text='https://example.com/v1')),
            self._update(callback_query=make_callback_query(data='cancel', from_user=make_user(id=123))),
        ]:
            self.assertIsNone(await guard_banned(update, context))
        self.assertEqual(state.user_requests, {})


class TestFakeDownload(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.ban.schedule_fake_failure')
    async def test_shows_loading_then_schedules_failure(self, mock_fail):
        state = make_state(animation_file_id='anim123')
        bot = AsyncMock()
        placeholder = AsyncMock()
        placeholder.message_id = 42
        message = make_message(chat_id=123, text='https://example.com/v1')
        message.reply_video.return_value = placeholder

        await fake_download(bot, state, message, 'https://example.com/v1')

        self.assertEqual(message.reply_video.call_args[1]['video'], 'anim123')
        self.assertEqual(message.reply_video.call_args[1]['caption'], 'https://example.com/v1')
        mock_fail.assert_called_once_with(bot, state, 'https://example.com/v1', chat_id='123', message_id=42)
        self.assertEqual(state.user_requests['123']['count'], 1)

    @patch('dasovbot.services.ban.schedule_fake_failure')
    async def test_without_animation_sends_nothing(self, mock_fail):
        # An ordinary failed download without an animation messages nobody either
        state = make_state(animation_file_id=None)
        message = make_message(chat_id=123, text='https://example.com/v1')

        await fake_download(AsyncMock(), state, message, 'https://example.com/v1')

        message.reply_video.assert_not_awaited()
        message.reply_text.assert_not_awaited()
        mock_fail.assert_not_called()
        self.assertEqual(state.user_requests['123']['count'], 1)

    @patch('dasovbot.services.ban.schedule_fake_failure')
    async def test_placeholder_error_is_swallowed(self, mock_fail):
        state = make_state(animation_file_id='anim123')
        message = make_message(chat_id=123, text='https://example.com/v1')
        message.reply_video.side_effect = Exception('blocked')

        await fake_download(AsyncMock(), state, message, 'https://example.com/v1')

        mock_fail.assert_not_called()
