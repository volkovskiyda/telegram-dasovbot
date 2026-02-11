"""
Integration tests with real Telegram API calls.

These tests validate that the bot can actually communicate with Telegram:
send messages, edit them, delete them, and use markdown formatting.

Requires .env.test with TEST_BOT_TOKEN, TEST_USER_ID, and TEST_CHAT_ID.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from tests.integration.base import IntegrationTestBase


class TestTelegramAPI(IntegrationTestBase):
    """Real API call tests — validates bot can communicate with Telegram"""

    async def test_get_me(self):
        """Bot.get_me() returns valid bot info"""
        me = await self.bot.get_me()
        self.assertTrue(me.is_bot)
        self.assertIsNotNone(me.username)

    async def test_send_message(self):
        """Bot can send a plain text message"""
        text = 'Integration test: send_message'
        message = await self.bot.send_message(
            chat_id=self.test_config.chat_id,
            text=text,
        )
        self.track_message(message)

        self.assertEqual(message.text, text)
        self.assertEqual(message.chat.id, self.test_config.chat_id)

    async def test_send_message_markdown(self):
        """Bot can send a Markdown-formatted message"""
        text = '*bold* _italic_ `code`'
        message = await self.bot.send_message(
            chat_id=self.test_config.chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )
        self.track_message(message)

        self.assertIsNotNone(message.text)

    async def test_edit_message_text(self):
        """Bot can edit an existing message"""
        original = await self.bot.send_message(
            chat_id=self.test_config.chat_id,
            text='Before edit',
        )
        self.track_message(original)

        edited = await self.bot.edit_message_text(
            chat_id=self.test_config.chat_id,
            message_id=original.message_id,
            text='After edit',
        )

        self.assertEqual(edited.text, 'After edit')

    async def test_delete_message(self):
        """Bot can delete a message"""
        message = await self.bot.send_message(
            chat_id=self.test_config.chat_id,
            text='To be deleted',
        )

        result = await self.bot.delete_message(
            chat_id=self.test_config.chat_id,
            message_id=message.message_id,
        )

        self.assertTrue(result)

    async def test_send_message_with_reply_markup(self):
        """Bot can send a message with inline keyboard"""
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(text='Button', callback_data='test')],
        ])
        message = await self.bot.send_message(
            chat_id=self.test_config.chat_id,
            text='Message with keyboard',
            reply_markup=markup,
        )
        self.track_message(message)

        self.assertIsNotNone(message.reply_markup)


if __name__ == '__main__':
    import unittest
    unittest.main()
