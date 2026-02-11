import asyncio
import os
import unittest
from typing import Optional

from dotenv import load_dotenv
from telegram import Bot, Update, User
from telegram.ext import Application

from dasovbot.config import Config
from dasovbot.downloader import init_downloader
from dasovbot.handlers import register_handlers
from dasovbot.state import BotState


class IntegrationTestConfig:
    """Configuration for integration tests"""

    def __init__(self):
        # Load test environment variables
        load_dotenv('.env.test')

        self.bot_token = os.getenv('TEST_BOT_TOKEN')
        self.user_id = int(os.getenv('TEST_USER_ID', '0'))
        self.chat_id = int(os.getenv('TEST_CHAT_ID', self.user_id))
        self.base_url = os.getenv('TEST_BASE_URL', '')
        self.read_timeout = int(os.getenv('TEST_READ_TIMEOUT', '30'))
        self.test_video_url = os.getenv('TEST_VIDEO_URL', 'https://example.com/video')

        if not self.bot_token:
            raise ValueError('TEST_BOT_TOKEN not set in .env.test')
        if not self.user_id:
            raise ValueError('TEST_USER_ID not set in .env.test')

    def to_bot_config(self) -> Config:
        """Convert to bot Config object"""
        return Config(
            bot_token=self.bot_token,
            base_url=self.base_url if self.base_url else '',
            developer_chat_id=str(self.user_id),
            developer_id=str(self.user_id),
            read_timeout=float(self.read_timeout),
            config_folder='/tmp/test_config',
        )


class IntegrationTestBase(unittest.IsolatedAsyncioTestCase):
    """Base class for integration tests with real Telegram bot"""

    test_config: IntegrationTestConfig
    application: Application
    bot: Bot
    state: BotState
    bot_user: Optional[User] = None

    @classmethod
    def setUpClass(cls):
        """Set up test configuration once for all tests"""
        cls.test_config = IntegrationTestConfig()

    async def asyncSetUp(self):
        """Set up bot application before each test"""
        # Create a minimal config for testing
        config = self.test_config.to_bot_config()

        # Initialize downloader with test config
        init_downloader(config)

        # Create a fresh state for each test
        self.state = BotState()

        # Build application
        builder = Application.builder().token(config.bot_token)
        if config.base_url:
            builder = builder.base_url(config.base_url)
        builder = builder.read_timeout(config.read_timeout)

        self.application = builder.build()
        self.bot = self.application.bot

        # Store state in bot_data
        self.application.bot_data['state'] = self.state

        # Register handlers
        register_handlers(self.application)

        # Initialize application
        await self.application.initialize()
        await self.application.start()

        # Get bot info
        self.bot_user = await self.bot.get_me()

    async def asyncTearDown(self):
        """Clean up after each test"""
        if self.application:
            await self.application.stop()
            await self.application.shutdown()

    async def send_message(self, text: str) -> Update:
        """
        Send a message to the bot and get the update
        Note: This requires manual interaction - you'll need to send the message
        """
        # For now, this is a helper to document the expected flow
        # In a real integration test, you would:
        # 1. Send message via bot.send_message to yourself
        # 2. Then manually send a message back to the bot
        # 3. Or use a test client that simulates updates
        raise NotImplementedError(
            "Direct message sending requires manual interaction or a test client. "
            "Use send_command_and_wait() or simulate_update() instead."
        )

    async def simulate_update(self, update: Update):
        """Simulate processing an update through the application"""
        await self.application.process_update(update)

    async def send_command(self, command: str, chat_id: Optional[int] = None):
        """Send a command to the test chat"""
        if chat_id is None:
            chat_id = self.test_config.chat_id

        return await self.bot.send_message(
            chat_id=chat_id,
            text=command
        )

    async def get_updates(self, timeout: int = 10) -> list[Update]:
        """Get pending updates for the bot"""
        return await self.bot.get_updates(timeout=timeout)

    async def clear_updates(self):
        """Clear all pending updates"""
        updates = await self.get_updates(timeout=1)
        if updates:
            last_update_id = updates[-1].update_id
            await self.bot.get_updates(offset=last_update_id + 1)


class IntegrationTestHelper:
    """Helper utilities for integration testing"""

    @staticmethod
    async def wait_for_condition(condition_fn, timeout: float = 5.0, interval: float = 0.1):
        """Wait for a condition to become true"""
        start = asyncio.get_event_loop().time()
        while True:
            if await condition_fn() if asyncio.iscoroutinefunction(condition_fn) else condition_fn():
                return True
            if asyncio.get_event_loop().time() - start > timeout:
                raise TimeoutError(f"Condition not met within {timeout}s")
            await asyncio.sleep(interval)

    @staticmethod
    def assert_message_contains(message, text: str):
        """Assert that message contains text"""
        assert text in message.text, f"Expected '{text}' in message '{message.text}'"

    @staticmethod
    def assert_message_equals(message, text: str):
        """Assert that message equals text"""
        assert message.text == text, f"Expected '{text}', got '{message.text}'"
