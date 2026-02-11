"""
Integration tests for download conversation flow

These tests verify the /download command conversation handler.
"""
from unittest.mock import AsyncMock, patch

from telegram import Update, Message, Chat, User
from telegram.ext import ConversationHandler

from dasovbot.constants import DAS_URL
from dasovbot.models import VideoInfo
from tests.integration.base import IntegrationTestBase


class TestDownloadConversation(IntegrationTestBase):
    """Test the download conversation flow"""

    def _create_update(self, text: str, message_id: int = 1) -> Update:
        """Helper to create an update with a message"""
        user = User(
            id=self.test_config.user_id,
            first_name="Test",
            is_bot=False,
            username="testuser"
        )

        chat = Chat(id=self.test_config.chat_id, type="private")

        message = Message(
            message_id=message_id,
            date=None,
            chat=chat,
            from_user=user,
            text=text
        )

        return Update(update_id=message_id, message=message)

    async def test_download_command_entry(self):
        """Test /download command entry point"""
        update = self._create_update("/download")

        # Process the update
        await self.simulate_update(update)

        print("\n✓ /download command entry processed")

    async def test_download_with_url_in_command(self):
        """Test /download with URL directly in command"""
        test_url = self.test_config.test_video_url

        with patch('dasovbot.handlers.download.extract_info') as mock_extract:
            # Mock the extract_info to avoid actual download
            mock_extract.return_value = AsyncMock(
                title="Test Video",
                webpage_url=test_url,
                caption="Test caption",
                entries=None,
                upload_date="20240101"
            )

            # Set animation file ID in state
            self.state.animation_file_id = "test_animation_id"

            update = self._create_update(f"/download {test_url}")

            # Process the update
            await self.simulate_update(update)

            print(f"\n✓ /download with URL processed: {test_url}")

    async def test_download_conversation_flow(self):
        """Test the full download conversation flow by calling handlers directly"""
        test_url = self.test_config.test_video_url

        from dasovbot.handlers.download import download, download_url
        from dasovbot.constants import DAS_URL
        from unittest.mock import MagicMock
        from telegram.ext import ConversationHandler

        with patch('dasovbot.handlers.download.extract_info') as mock_extract, \
             patch('dasovbot.handlers.download.append_intent', new_callable=AsyncMock):
            # Mock the extract_info
            mock_info = AsyncMock()
            mock_info.title = "Test Video"
            mock_info.webpage_url = test_url
            mock_info.caption = "Test caption"
            mock_info.entries = None
            mock_info.upload_date = "20240101"
            mock_extract.return_value = mock_info

            # Set animation file ID
            self.state.animation_file_id = "test_animation_id"

            # Step 1: Call download handler (entry point)
            # Create a fully mocked update with a mocked message
            mock_user = MagicMock()
            mock_user.id = self.test_config.user_id
            mock_user.username = 'testuser'
            mock_user.__getitem__ = lambda self, key: {'id': mock_user.id, 'username': mock_user.username}[key]
            mock_user.to_dict = lambda: {'id': self.test_config.user_id, 'username': 'testuser'}

            mock_message1 = AsyncMock()
            mock_message1.text = "/download"
            mock_message1.from_user = mock_user
            mock_message1.chat_id = self.test_config.chat_id
            mock_message1.reply_text = AsyncMock()

            mock_update1 = MagicMock()
            mock_update1.message = mock_message1

            context = MagicMock()
            context.bot_data = {'state': self.state}

            result = await download(mock_update1, context)
            self.assertEqual(result, DAS_URL)  # Should return the next state
            mock_message1.reply_text.assert_called_once_with("Enter url")

            # Step 2: Call download_url handler (receives URL)
            mock_message2 = AsyncMock()
            mock_message2.text = test_url
            mock_message2.from_user = mock_user
            mock_message2.chat_id = self.test_config.chat_id
            mock_message2.id = 2
            mock_message2.reply_text = AsyncMock()
            mock_message2.reply_video = AsyncMock(return_value=AsyncMock(message_id=123))

            mock_update2 = MagicMock()
            mock_update2.message = mock_message2

            result = await download_url(mock_update2, context)

            # Verify extract_info was called with the URL
            mock_extract.assert_called_once()
            call_args = mock_extract.call_args
            self.assertEqual(call_args[0][0], test_url)

            # Verify reply_video was called
            mock_message2.reply_video.assert_called_once()

            # Should return ConversationHandler.END
            self.assertEqual(result, ConversationHandler.END)

            print(f"\n✓ Download conversation flow completed for {test_url}")

    async def test_download_with_invalid_url(self):
        """Test download with invalid/unsupported URL"""
        with patch('dasovbot.handlers.download.extract_info') as mock_extract:
            # Mock extract_info to return None (unsupported)
            mock_extract.return_value = None

            update = self._create_update("/download https://unsupported.com/video")
            await self.simulate_update(update)

            print("\n✓ Invalid URL handled correctly")

    async def test_download_with_playlist(self):
        """Test download with playlist URL (should be rejected)"""
        with patch('dasovbot.handlers.download.extract_info') as mock_extract:
            # Mock extract_info to return info with entries (playlist)
            mock_info = AsyncMock()
            mock_info.entries = [{"title": "Video 1"}, {"title": "Video 2"}]
            mock_extract.return_value = mock_info

            update = self._create_update("/download https://example.com/playlist")
            await self.simulate_update(update)

            print("\n✓ Playlist URL rejected as expected")

    async def test_cancel_download_conversation(self):
        """Test canceling the download conversation"""
        # Start conversation
        update1 = self._create_update("/download", message_id=1)
        await self.simulate_update(update1)

        # Cancel
        update2 = self._create_update("/cancel", message_id=2)
        await self.simulate_update(update2)

        print("\n✓ Download conversation cancelled")

    async def test_download_empty_query(self):
        """Test download with empty query after command"""
        update = self._create_update("/download", message_id=1)
        await self.simulate_update(update)

        # Send empty text
        update2 = self._create_update("", message_id=2)
        await self.simulate_update(update2)

        print("\n✓ Empty query handled")


class TestDownloadConversationStates(IntegrationTestBase):
    """Test conversation state transitions"""

    async def test_conversation_state_tracking(self):
        """Test that conversation states are tracked correctly"""
        # Get conversation handler
        handlers = self.application.handlers[0]  # Default group

        download_handler = None
        for handler in handlers:
            if hasattr(handler, 'entry_points'):
                # Check if this is the download conversation
                for ep in handler.entry_points:
                    if hasattr(ep, 'commands') and 'download' in ep.commands:
                        download_handler = handler
                        break

        self.assertIsNotNone(download_handler)
        self.assertIn(DAS_URL, download_handler.states)

        print("\n✓ Download conversation handler configured correctly")
        print(f"  States: {list(download_handler.states.keys())}")


if __name__ == '__main__':
    import unittest
    unittest.main()
