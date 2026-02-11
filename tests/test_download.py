import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from telegram.ext import ConversationHandler

from dasovbot.constants import DAS_URL
from dasovbot.models import VideoInfo
from tests.helpers import make_user, make_message, make_update, make_context, make_state


class TestDownload(unittest.IsolatedAsyncioTestCase):

    async def test_without_url_returns_das_url(self):
        message = make_message(text='/download')
        update = make_update(message=message)

        from dasovbot.handlers.download import download
        result = await download(update, None)

        message.reply_text.assert_awaited_once_with('Enter url')
        self.assertEqual(result, DAS_URL)

    @patch('dasovbot.handlers.download.download_url', new_callable=AsyncMock)
    async def test_with_url_delegates(self, mock_download_url):
        mock_download_url.return_value = ConversationHandler.END
        message = make_message(text='/download https://example.com/v1')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.download import download
        result = await download(update, context)

        mock_download_url.assert_awaited_once()
        self.assertEqual(result, ConversationHandler.END)


class TestDownloadUrl(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.download.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.download.extract_info', new_callable=AsyncMock)
    async def test_empty_query_ends(self, mock_extract, mock_append):
        message = make_message(text='')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.download import download_url
        result = await download_url(update, context)

        self.assertEqual(result, ConversationHandler.END)
        mock_extract.assert_not_called()

    @patch('dasovbot.handlers.download.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.download.extract_info', new_callable=AsyncMock)
    async def test_unsupported_url_none(self, mock_extract, mock_append):
        mock_extract.return_value = None

        message = make_message(text='https://example.com/bad')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.download import download_url
        result = await download_url(update, context)

        message.reply_text.assert_awaited_once()
        self.assertIn('Unsupported', message.reply_text.call_args[0][0])
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.download.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.download.extract_info', new_callable=AsyncMock)
    async def test_unsupported_url_has_entries(self, mock_extract, mock_append):
        info = VideoInfo(title='Playlist', entries=[{'title': 'V1'}])
        mock_extract.return_value = info

        message = make_message(text='https://example.com/playlist')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.download import download_url
        result = await download_url(update, context)

        message.reply_text.assert_awaited_once()
        self.assertIn('Unsupported', message.reply_text.call_args[0][0])
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.download.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.download.extract_info', new_callable=AsyncMock)
    async def test_successful_download(self, mock_extract, mock_append):
        info = VideoInfo(title='Test Video', caption='cap', webpage_url='https://example.com/v1', upload_date='20240101')
        mock_extract.return_value = info

        user = make_user(id=123, username='testuser')
        user.to_dict.return_value = {'id': 123, 'username': 'testuser'}
        video_reply = AsyncMock()
        video_reply.message_id = 42

        message = make_message(chat_id=123, text='https://example.com/v1', from_user=user)
        message.reply_video.return_value = video_reply
        message.id = 1

        state = make_state(animation_file_id='anim123')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.download import download_url
        result = await download_url(update, context)

        message.reply_video.assert_awaited_once()
        self.assertEqual(message.reply_video.call_args[1]['video'], 'anim123')
        mock_append.assert_awaited_once()
        self.assertEqual(state.users['123'], {'id': 123, 'username': 'testuser'})
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.download.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.download.extract_info', new_callable=AsyncMock)
    async def test_reply_video_error_still_stores_user(self, mock_extract, mock_append):
        info = VideoInfo(title='Test Video', caption='cap', webpage_url='https://example.com/v1')
        mock_extract.return_value = info

        user = make_user(id=123, username='testuser')
        user.to_dict.return_value = {'id': 123, 'username': 'testuser'}

        message = make_message(chat_id=123, text='https://example.com/v1', from_user=user)
        message.reply_video.side_effect = Exception('network error')
        message.id = 1

        state = make_state(animation_file_id='anim123')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.download import download_url
        result = await download_url(update, context)

        # User is still stored even if reply_video fails
        self.assertEqual(state.users['123'], {'id': 123, 'username': 'testuser'})
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.download.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.download.extract_info', new_callable=AsyncMock)
    async def test_append_intent_receives_source_and_title(self, mock_extract, mock_append):
        info = VideoInfo(title='My Video', caption='cap', webpage_url='https://example.com/v1', upload_date='20240515')
        mock_extract.return_value = info

        user = make_user(id=123)
        user.to_dict.return_value = {'id': 123, 'username': 'testuser'}
        video_reply = AsyncMock()
        video_reply.message_id = 42

        message = make_message(chat_id=123, text='https://example.com/v1', from_user=user)
        message.reply_video.return_value = video_reply
        message.id = 1

        state = make_state(animation_file_id='anim123')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.download import download_url
        await download_url(update, context)

        call_kwargs = mock_append.call_args[1]
        self.assertEqual(call_kwargs['source'], 'download')
        self.assertEqual(call_kwargs['title'], 'My Video')
        self.assertEqual(call_kwargs['upload_date'], '20240515')


if __name__ == '__main__':
    unittest.main()
