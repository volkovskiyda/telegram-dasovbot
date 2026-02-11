import unittest
from typing import Optional

from dotenv import load_dotenv
from telegram import Bot, Chat, Message, Update, User
from telegram.ext import Application

from dasovbot.config import Config
from dasovbot.downloader import init_downloader
from dasovbot.handlers import register_handlers
from dasovbot.state import BotState

import os


class IntegrationTestConfig:
    """Configuration for integration tests"""

    def __init__(self):
        # Load test environment variables
        load_dotenv('.env.test')

        self.bot_token = os.getenv('TEST_BOT_TOKEN')
        self.user_id = int(os.getenv('TEST_USER_ID', '0'))
        self.chat_id = int(os.getenv('TEST_CHAT_ID', str(self.user_id)))
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
        try:
            cls.test_config = IntegrationTestConfig()
        except ValueError as e:
            raise unittest.SkipTest(str(e))

    async def asyncSetUp(self):
        """Set up bot application before each test"""
        self.sent_message_ids = []

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
        for msg_id in self.sent_message_ids:
            try:
                await self.bot.delete_message(self.test_config.chat_id, msg_id)
            except Exception:
                pass

        if self.application:
            await self.application.stop()
            await self.application.shutdown()

    def track_message(self, message):
        """Track a sent message for cleanup in tearDown"""
        self.sent_message_ids.append(message.message_id)
        return message

    def make_update(self, text, message_id=1):
        """Create a fake Update with a message for handler testing"""
        user = User(id=self.test_config.user_id, first_name='Test', is_bot=False, username='testuser')
        chat = Chat(id=self.test_config.chat_id, type='private')
        message = Message(message_id=message_id, date=None, chat=chat, from_user=user, text=text)
        return Update(update_id=message_id, message=message)

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
