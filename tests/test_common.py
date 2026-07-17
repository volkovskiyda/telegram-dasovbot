import unittest
from unittest.mock import MagicMock

from telegram.ext import ConversationHandler

from tests.helpers import make_user, make_message, make_update, make_context


class TestStart(unittest.IsolatedAsyncioTestCase):

    async def test_replies_with_welcome(self):
        user = make_user(id=123, username='testuser')
        user.__getitem__ = lambda self, key: {'username': 'testuser', 'id': 123}[key]
        message = make_message(chat_id=123, from_user=user)
        update = make_update(message=message)

        from dasovbot.handlers.common import start
        await start(update, None)

        message.reply_text.assert_awaited_once()
        text = message.reply_text.call_args[0][0]
        self.assertIn('Welcome', text)
        self.assertIn('@testuser', text)

    async def test_falls_back_to_first_name_without_username(self):
        user = make_user(id=123, username=None)
        user.first_name = 'Alice'
        message = make_message(chat_id=123, from_user=user)
        update = make_update(message=message)

        from dasovbot.handlers.common import start
        await start(update, None)

        text = message.reply_text.call_args[0][0]
        self.assertIn('Hey, Alice.', text)
        self.assertNotIn('@None', text)

    async def test_plain_greeting_without_username_or_first_name(self):
        user = make_user(id=123, username=None)
        user.first_name = None
        message = make_message(chat_id=123, from_user=user)
        update = make_update(message=message)

        from dasovbot.handlers.common import start
        await start(update, None)

        text = message.reply_text.call_args[0][0]
        self.assertIn('Hey.', text)
        self.assertNotIn('@None', text)


class TestHelpCommand(unittest.IsolatedAsyncioTestCase):

    async def test_replies_markdown(self):
        message = make_message()
        update = make_update(message=message)

        from dasovbot.handlers.common import help_command
        await help_command(update, None)

        message.reply_markdown.assert_awaited_once()
        text = message.reply_markdown.call_args[0][0]
        self.assertIn('/download', text)
        self.assertIn('/subscribe', text)


class TestUnknown(unittest.IsolatedAsyncioTestCase):

    async def test_replies_unknown(self):
        message = make_message()
        update = make_update(message=message)

        from dasovbot.handlers.common import unknown
        await unknown(update, None)

        message.reply_text.assert_awaited_once()
        text = message.reply_text.call_args[0][0]
        self.assertIn('Unknown command', text)


class TestErrorHandler(unittest.IsolatedAsyncioTestCase):

    async def test_logs_error_without_raising(self):
        context = make_context()
        context.error = ValueError('boom')

        from dasovbot.handlers.common import error_handler
        with self.assertLogs('dasovbot.handlers.common', level='ERROR') as cm:
            await error_handler(make_update(), context)

        self.assertIn('Unhandled handler error', '\n'.join(cm.output))
        self.assertIn('boom', '\n'.join(cm.output))

    async def test_handles_missing_error(self):
        context = make_context()
        context.error = None

        from dasovbot.handlers.common import error_handler
        with self.assertLogs('dasovbot.handlers.common', level='ERROR'):
            await error_handler(None, context)


class TestCancel(unittest.IsolatedAsyncioTestCase):

    async def test_returns_end(self):
        user = make_user()
        message = make_message(from_user=user)
        update = make_update(message=message)

        from dasovbot.handlers.common import cancel
        result = await cancel(update, None)

        self.assertEqual(result, ConversationHandler.END)
        message.reply_text.assert_awaited_once()
        text = message.reply_text.call_args[0][0]
        self.assertIn('Cancelled', text)


if __name__ == '__main__':
    unittest.main()
