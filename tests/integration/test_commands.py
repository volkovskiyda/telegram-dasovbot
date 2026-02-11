"""
Integration tests for basic bot commands

These tests require a real Telegram bot token and manual verification.
To run these tests:
1. Copy .env.test.example to .env.test
2. Fill in your TEST_BOT_TOKEN and TEST_USER_ID
3. Run: python -m pytest tests/integration/test_commands.py -v
"""
import asyncio
from unittest.mock import MagicMock

from telegram import Update, Message, Chat, User

from tests.integration.base import IntegrationTestBase


class TestBasicCommands(IntegrationTestBase):
    """Test basic bot commands like /start and /help"""

    async def test_bot_initialization(self):
        """Test that bot initializes correctly and can get bot info"""
        self.assertIsNotNone(self.bot_user)
        self.assertTrue(self.bot_user.is_bot)
        print(f"\n✓ Bot initialized: @{self.bot_user.username}")

    async def test_start_command_handler(self):
        """Test /start command handler"""
        # Create a mock update for /start command
        user = User(
            id=self.test_config.user_id,
            first_name="Test",
            is_bot=False,
            username="testuser"
        )

        chat = Chat(id=self.test_config.chat_id, type="private")

        message = Message(
            message_id=1,
            date=None,
            chat=chat,
            from_user=user,
            text="/start"
        )

        update = Update(update_id=1, message=message)

        # Process the update through the application
        await self.simulate_update(update)

        # In a real integration test, you would check the response
        # For now, we just verify it doesn't crash
        print("\n✓ /start command processed successfully")

    async def test_help_command_handler(self):
        """Test /help command handler"""
        user = User(
            id=self.test_config.user_id,
            first_name="Test",
            is_bot=False,
            username="testuser"
        )

        chat = Chat(id=self.test_config.chat_id, type="private")

        message = Message(
            message_id=2,
            date=None,
            chat=chat,
            from_user=user,
            text="/help"
        )

        update = Update(update_id=2, message=message)

        # Process the update
        await self.simulate_update(update)

        print("\n✓ /help command processed successfully")

    async def test_unknown_command_handler(self):
        """Test unknown command handler"""
        user = User(
            id=self.test_config.user_id,
            first_name="Test",
            is_bot=False,
            username="testuser"
        )

        chat = Chat(id=self.test_config.chat_id, type="private")

        message = Message(
            message_id=3,
            date=None,
            chat=chat,
            from_user=user,
            text="/unknowncommand"
        )

        update = Update(update_id=3, message=message)

        # Process the update
        await self.simulate_update(update)

        print("\n✓ Unknown command handled successfully")

    async def test_bot_can_send_message(self):
        """Test that bot can send messages to test chat"""
        message = await self.send_command("/start")

        self.assertIsNotNone(message)
        self.assertEqual(message.chat.id, self.test_config.chat_id)

        print(f"\n✓ Bot sent message successfully to chat {self.test_config.chat_id}")
        print(f"  Message preview: {message.text[:50]}...")

    async def test_get_chat_info(self):
        """Test getting chat information"""
        chat = await self.bot.get_chat(self.test_config.chat_id)

        self.assertIsNotNone(chat)
        print(f"\n✓ Got chat info for {self.test_config.chat_id}")
        print(f"  Chat type: {chat.type}")


class TestCommandEndToEnd(IntegrationTestBase):
    """
    End-to-end tests that require manual interaction

    These tests will send real messages to your test bot and expect you
    to manually respond or verify the bot's behavior.

    Set ENABLE_E2E_TESTS=1 in .env.test to enable these tests.
    """

    def setUp(self):
        """Check if E2E tests are enabled"""
        import os
        if not os.getenv('ENABLE_E2E_TESTS'):
            self.skipTest("E2E tests disabled. Set ENABLE_E2E_TESTS=1 to enable")

    async def test_start_command_e2e(self):
        """
        E2E test for /start command

        This test will:
        1. Clear any pending updates
        2. Send you a message asking you to send /start to the bot
        3. Wait for your response
        4. Verify the bot's response
        """
        # Clear pending updates
        await self.clear_updates()

        # Send instruction message
        instruction_msg = await self.bot.send_message(
            chat_id=self.test_config.chat_id,
            text="🤖 Integration Test: Please send /start to the bot now"
        )

        print("\n⏳ Waiting for /start command... (you have 30 seconds)")

        # Wait for the update
        timeout = 30
        start_time = asyncio.get_event_loop().time()

        while True:
            updates = await self.bot.get_updates(timeout=5)

            for update in updates:
                if (update.message and
                    update.message.text == "/start" and
                    update.message.from_user.id == self.test_config.user_id):

                    # Process through application to get response
                    await self.simulate_update(update)

                    # Mark as processed
                    await self.bot.get_updates(offset=update.update_id + 1)

                    print("✓ Received and processed /start command")
                    return

            if asyncio.get_event_loop().time() - start_time > timeout:
                self.fail("Timeout waiting for /start command")

            await asyncio.sleep(1)


if __name__ == '__main__':
    import unittest
    unittest.main()
