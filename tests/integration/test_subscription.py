"""
Integration tests for subscription handlers.

These tests mock get_ydl (no real video extraction) but let handlers call
the real Telegram API for reply_text / reply_markdown.

Requires .env.test with TEST_BOT_TOKEN, TEST_USER_ID, and TEST_CHAT_ID.
"""
from unittest.mock import patch, MagicMock

from dasovbot.models import Subscription
from tests.integration.base import IntegrationTestBase


class TestSubscriptionHandlers(IntegrationTestBase):
    """Subscription handler tests with mocked ydl but real bot API calls"""

    async def test_subscription_list_empty(self):
        """subscription_list with no subscriptions doesn't crash"""
        update = self.make_update('/subscriptions')
        await self.simulate_update(update)

    async def test_subscription_list_with_data(self):
        """subscription_list with pre-populated data doesn't crash"""
        chat_id = str(self.test_config.chat_id)
        self.state.subscriptions['https://example.com/playlist'] = Subscription(
            chat_ids=[chat_id],
            title='Test Playlist',
            uploader='TestUploader',
            uploader_videos='https://example.com/videos',
        )

        update = self.make_update('/subscriptions')
        await self.simulate_update(update)

    async def test_subscribe_entry_no_url(self):
        """subscribe entry point without URL asks for input"""
        update = self.make_update('/subscribe')
        await self.simulate_update(update)

    async def test_subscribe_url_unsupported(self):
        """subscribe with unsupported URL returns gracefully"""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {'uploader_url': None}

        with patch('dasovbot.handlers.subscription.get_ydl', return_value=mock_ydl):
            update = self.make_update('/subscribe https://unsupported.example.com')
            await self.simulate_update(update)

    async def test_unsubscribe_no_subs(self):
        """unsubscribe with empty subscriptions doesn't crash"""
        update = self.make_update('/unsubscribe')
        await self.simulate_update(update)

    async def test_multiple_subscribe_entry(self):
        """multiple_subscribe entry point asks for URLs"""
        update = self.make_update('/multiple_subscribe')
        await self.simulate_update(update)


if __name__ == '__main__':
    import unittest
    unittest.main()
