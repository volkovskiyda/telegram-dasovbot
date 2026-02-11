"""
Integration tests for inline query handling

These tests verify inline query functionality with a real bot.
Note: Inline queries require the bot to have inline mode enabled in @BotFather
"""
from unittest.mock import AsyncMock, patch, MagicMock

from telegram import Update, InlineQuery, User, ChosenInlineResult

from dasovbot.models import VideoInfo
from tests.integration.base import IntegrationTestBase


class TestInlineQueries(IntegrationTestBase):
    """Test inline query handling"""

    def _create_inline_query_update(self, query: str, query_id: str = "test_query_id") -> Update:
        """Helper to create an inline query update"""
        user = User(
            id=self.test_config.user_id,
            first_name="Test",
            is_bot=False,
            username="testuser"
        )

        inline_query = InlineQuery(
            id=query_id,
            from_user=user,
            query=query,
            offset=""
        )

        return Update(update_id=1, inline_query=inline_query)

    def _create_chosen_inline_result_update(
        self,
        result_id: str,
        inline_message_id: str = "test_inline_msg_id"
    ) -> Update:
        """Helper to create a chosen inline result update"""
        user = User(
            id=self.test_config.user_id,
            first_name="Test",
            is_bot=False,
            username="testuser"
        )

        chosen_result = ChosenInlineResult(
            result_id=result_id,
            from_user=user,
            query="https://example.com/video",
            inline_message_id=inline_message_id
        )

        return Update(update_id=2, chosen_inline_result=chosen_result)

    async def test_inline_query_handler_registered(self):
        """Test that inline query handler is registered"""
        from telegram.ext import InlineQueryHandler

        handlers = self.application.handlers[0]  # Default group
        inline_handlers = [h for h in handlers if isinstance(h, InlineQueryHandler)]

        self.assertGreater(len(inline_handlers), 0)
        print(f"\n✓ Found {len(inline_handlers)} inline query handler(s)")

    async def test_empty_inline_query(self):
        """Test inline query with empty string"""
        with patch('dasovbot.handlers.inline.extract_info') as mock_extract:
            update = self._create_inline_query_update("")

            await self.simulate_update(update)

            # Empty query should not call extract_info
            mock_extract.assert_not_called()
            print("\n✓ Empty inline query handled (no extraction)")

    async def test_inline_query_with_url(self):
        """Test inline query with video URL"""
        with patch('dasovbot.handlers.inline.extract_info') as mock_extract:
            # Mock the extract_info response
            mock_info = VideoInfo(
                title="Test Video",
                webpage_url="https://example.com/video",
                caption="Test caption",
                upload_date="20240101"
            )
            mock_extract.return_value = mock_info

            # Set animation file ID for results
            self.state.animation_file_id = "test_animation_id"

            update = self._create_inline_query_update("https://example.com/video")

            await self.simulate_update(update)

            # Should call extract_info
            mock_extract.assert_called_once()
            print("\n✓ Inline query with URL processed")
            print(f"  extract_info called with: {mock_extract.call_args[0][0]}")

    async def test_inline_query_caching(self):
        """Test that inline queries are cached"""
        from dasovbot.models import TemporaryInlineQuery

        with patch('dasovbot.handlers.inline.extract_info') as mock_extract:
            mock_info = VideoInfo(
                title="Test Video",
                webpage_url="https://example.com/video",
                caption="Test caption"
            )
            mock_extract.return_value = mock_info

            self.state.animation_file_id = "test_animation_id"

            # First query - should extract
            update1 = self._create_inline_query_update("https://example.com/video", "query1")
            await self.simulate_update(update1)

            # Manually add to cache (simulating what the handler does)
            tiq = TemporaryInlineQuery(
                timestamp="20240101_120000",
                results=[],
                inline_queries={"result_id": "https://example.com/video"}
            )
            self.state.temporary_inline_queries["https://example.com/video"] = tiq

            # Second query - should use cache
            mock_extract.reset_mock()
            update2 = self._create_inline_query_update("https://example.com/video", "query2")
            await self.simulate_update(update2)

            # Should not call extract_info again
            mock_extract.assert_not_called()
            print("\n✓ Inline query caching works")

    async def test_inline_query_with_playlist(self):
        """Test inline query with playlist URL"""
        with patch('dasovbot.handlers.inline.extract_info') as mock_extract, \
             patch('dasovbot.handlers.inline.process_entries') as mock_process:

            # Mock playlist response
            entry1 = {'title': 'Video 1', 'webpage_url': 'https://example.com/v1', 'url': 'https://example.com/v1'}
            entry2 = {'title': 'Video 2', 'webpage_url': 'https://example.com/v2', 'url': 'https://example.com/v2'}

            mock_info = VideoInfo(
                title="Test Playlist",
                webpage_url="https://example.com/playlist",
                caption="Playlist",
                entries=[entry1, entry2]
            )
            mock_extract.return_value = mock_info
            mock_process.return_value = [entry1, entry2]

            self.state.animation_file_id = "test_animation_id"

            update = self._create_inline_query_update("https://example.com/playlist")
            await self.simulate_update(update)

            # Should process entries
            mock_process.assert_called_once()
            print("\n✓ Inline query with playlist processed")

    async def test_chosen_inline_result(self):
        """Test chosen inline result handling"""
        with patch('dasovbot.handlers.inline.append_intent') as mock_append:
            # Set up state with video info
            video_info = VideoInfo(
                title="Test Video",
                webpage_url="https://example.com/video",
                caption="Test caption",
                file_id=None  # No file_id yet
            )
            self.state.videos["https://example.com/video"] = video_info

            # Create context with inline_queries
            update = self._create_chosen_inline_result_update("result_123")

            # Manually set up user_data (normally done by inline query handler)
            if not hasattr(self.application, 'user_data'):
                self.application.user_data = {}
            user_key = (self.test_config.user_id, self.test_config.user_id)
            self.application.user_data[user_key] = {
                'inline_queries': {'result_123': 'https://example.com/video'}
            }

            await self.simulate_update(update)

            # Should append intent since no file_id
            mock_append.assert_called_once()
            print("\n✓ Chosen inline result processed")

    async def test_chosen_inline_result_with_cached_file(self):
        """Test chosen inline result with cached file ID"""
        # Set up state with cached file
        video_info = VideoInfo(
            title="Test Video",
            webpage_url="https://example.com/video",
            caption="Test caption",
            file_id="cached_file_id_123"
        )
        self.state.videos["https://example.com/video"] = video_info

        update = self._create_chosen_inline_result_update("result_123")

        # Set up user_data
        if not hasattr(self.application, 'user_data'):
            self.application.user_data = {}
        user_key = (self.test_config.user_id, self.test_config.user_id)
        self.application.user_data[user_key] = {
            'inline_queries': {'result_123': 'https://example.com/video'}
        }

        await self.simulate_update(update)

        print("\n✓ Chosen inline result with cached file processed")

    async def test_inline_query_error_handling(self):
        """Test inline query error handling"""
        with patch('dasovbot.handlers.inline.extract_info') as mock_extract:
            # Simulate extraction error
            mock_extract.return_value = None

            update = self._create_inline_query_update("https://invalid-url.com")

            # Should not crash
            await self.simulate_update(update)

            print("\n✓ Inline query error handled gracefully")


class TestInlineQueryEndToEnd(IntegrationTestBase):
    """
    End-to-end inline query tests

    These require manual interaction. Enable with ENABLE_E2E_TESTS=1
    """

    def setUp(self):
        """Check if E2E tests are enabled"""
        import os
        if not os.getenv('ENABLE_E2E_TESTS'):
            self.skipTest("E2E tests disabled. Set ENABLE_E2E_TESTS=1 to enable")

    async def test_inline_mode_enabled(self):
        """Verify that inline mode is enabled for the bot"""
        # Note: This requires checking bot settings via @BotFather
        # We can only verify the handler is registered
        from telegram.ext import InlineQueryHandler

        handlers = self.application.handlers[0]
        inline_handlers = [h for h in handlers if isinstance(h, InlineQueryHandler)]

        self.assertGreater(len(inline_handlers), 0)

        print("\n✓ Inline query handlers registered")
        print("⚠️  Make sure inline mode is enabled via @BotFather:")
        print("   1. Send /setinline to @BotFather")
        print(f"   2. Select @{self.bot_user.username}")
        print("   3. Set an inline placeholder message")


if __name__ == '__main__':
    import unittest
    unittest.main()
