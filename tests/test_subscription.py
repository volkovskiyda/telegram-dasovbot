import unittest
from unittest.mock import MagicMock, patch

from telegram.ext import ConversationHandler

from dasovbot.constants import (
    SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW,
    UNSUBSCRIBE_PLAYLIST,
)
from dasovbot.models import Subscription
from tests.helpers import (
    make_message, make_callback_query,
    make_update, make_context, make_state,
)


class TestSubscriptionList(unittest.IsolatedAsyncioTestCase):

    async def test_shows_subscriptions(self):
        subs = {
            'https://example.com/c1/videos': Subscription(chat_ids=['123'], title='Channel 1', uploader='C1'),
            'https://example.com/c2/videos': Subscription(chat_ids=['123'], title='Channel 2', uploader='C2'),
        }
        state = make_state(subscriptions=subs)
        message = make_message(chat_id=123)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import subscription_list
        await subscription_list(update, context)

        message.reply_markdown.assert_awaited_once()
        text = message.reply_markdown.call_args[0][0]
        self.assertIn('Channel 1', text)
        self.assertIn('Channel 2', text)

    async def test_no_subscriptions(self):
        state = make_state(subscriptions={})
        message = make_message(chat_id=123)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import subscription_list
        await subscription_list(update, context)

        message.reply_text.assert_awaited_once_with('No subscriptions')

    async def test_only_own_subscriptions(self):
        subs = {
            'https://example.com/c1/videos': Subscription(chat_ids=['123'], title='Mine', uploader='C1'),
            'https://example.com/c2/videos': Subscription(chat_ids=['999'], title='Other', uploader='C2'),
        }
        state = make_state(subscriptions=subs)
        message = make_message(chat_id=123)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import subscription_list
        await subscription_list(update, context)

        message.reply_markdown.assert_awaited_once()
        text = message.reply_markdown.call_args[0][0]
        self.assertIn('Mine', text)
        self.assertNotIn('Other', text)


class TestSubscribe(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.subscribe_url')
    async def test_with_url_delegates(self, mock_sub_url):
        mock_sub_url.return_value = SUBSCRIBE_PLAYLIST
        message = make_message(text='/subscribe https://example.com/c1')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe
        result = await subscribe(update, context)

        mock_sub_url.assert_awaited_once()
        self.assertEqual(result, SUBSCRIBE_PLAYLIST)

    async def test_without_url_prompts(self):
        message = make_message(text='/subscribe')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe
        result = await subscribe(update, context)

        message.reply_text.assert_awaited_once_with('Enter url')
        self.assertEqual(result, SUBSCRIBE_URL)


class TestSubscribeUrl(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_empty_query_ends(self, mock_get_ydl):
        message = make_message(text='')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe_url
        result = await subscribe_url(update, context)

        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_no_uploader_url(self, mock_get_ydl):
        ydl = MagicMock()
        ydl.extract_info.return_value = {'title': 'Video', 'url': 'https://example.com'}
        mock_get_ydl.return_value = ydl

        message = make_message(text='https://example.com/v1')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe_url
        result = await subscribe_url(update, context)

        message.reply_text.assert_awaited_once()
        self.assertIn('Unsupported', message.reply_text.call_args[0][0])
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_success_with_playlists(self, mock_get_ydl):
        ydl = MagicMock()
        ydl.extract_info.side_effect = [
            # First call: extract_info(query)
            {
                'uploader_url': 'https://example.com/c1',
                'uploader': 'Channel1',
                'uploader_id': 'c1',
            },
            # Second call: extract_info(uploader_url) since uploader_url != query
            {
                'uploader_url': 'https://example.com/c1',
            },
            # Third call: extract_info(playlists_url)
            {
                'entries': [
                    {'title': 'Playlist1', 'webpage_url': 'https://example.com/p1', 'url': 'https://example.com/p1'},
                ],
                'uploader': 'Channel1',
                'uploader_id': 'c1',
            },
            # Fourth call: extract_info(uploader_streams) — for streams check
            Exception('no streams'),
        ]
        mock_get_ydl.return_value = ydl

        message = make_message(text='https://example.com/video1')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe_url
        result = await subscribe_url(update, context)

        message.reply_markdown.assert_awaited_once()
        self.assertEqual(result, SUBSCRIBE_PLAYLIST)

    @patch('dasovbot.handlers.subscription.subscribe_playlist')
    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_playlists_fail_falls_through(self, mock_get_ydl, mock_sub_playlist):
        mock_sub_playlist.return_value = SUBSCRIBE_SHOW
        ydl = MagicMock()
        ydl.extract_info.side_effect = [
            # First call: extract_info(query)
            {
                'uploader_url': 'https://example.com/c1',
            },
            # Second call: extract_info(uploader_url) since uploader_url != query
            {},
            # Third call: extract_info(playlists_url) — raises
            Exception('playlists not found'),
        ]
        mock_get_ydl.return_value = ydl

        message = make_message(text='https://example.com/video1')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe_url
        result = await subscribe_url(update, context)

        self.assertEqual(context.user_data['uploader_videos'], 'https://example.com/c1/videos')
        mock_sub_playlist.assert_awaited_once()
        self.assertEqual(result, SUBSCRIBE_SHOW)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_extract_exception_replies_error(self, mock_get_ydl):
        ydl = MagicMock()
        ydl.extract_info.side_effect = Exception('boom')
        mock_get_ydl.return_value = ydl

        message = make_message(text='https://example.com/bad')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe_url
        result = await subscribe_url(update, context)

        message.reply_text.assert_awaited_once()
        self.assertIn('Error', message.reply_text.call_args[0][0])
        self.assertEqual(result, ConversationHandler.END)


class TestSubscribePlaylist(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_callback_cancel(self, mock_get_ydl):
        message = make_message()
        cq = make_callback_query(data='cancel', message=message)
        update = make_update(callback_query=cq)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, context)

        message.delete.assert_awaited_once()
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_creates_subscription(self, mock_get_ydl):
        ydl = MagicMock()
        ydl.extract_info.return_value = {
            'title': 'Playlist Title',
            'uploader': 'Uploader1',
            'uploader_url': 'https://example.com/c1',
        }
        mock_get_ydl.return_value = ydl

        state = make_state()
        playlist_id = 'pid1'
        playlists = {
            'first': {'title': 'Uploader1 Videos', 'url': 'https://example.com/c1/videos'},
            'pid1': {'title': 'My Playlist', 'url': 'https://example.com/p1'},
        }

        message = make_message(chat_id=123)
        cq = make_callback_query(data=playlist_id, message=message)
        update = make_update(callback_query=cq)
        context = make_context(
            state=state,
            user_data={'playlists': playlists},
        )

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, context)

        self.assertIn('https://example.com/p1', state.subscriptions)
        sub = state.subscriptions['https://example.com/p1']
        self.assertIn('123', sub.chat_ids)
        self.assertEqual(sub.title, 'My Playlist')
        self.assertEqual(result, SUBSCRIBE_SHOW)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_already_subscribed(self, mock_get_ydl):
        existing_sub = Subscription(chat_ids=['123'], title='Existing', uploader='U', uploader_videos='v')
        state = make_state(subscriptions={'https://example.com/p1': existing_sub})

        playlist_id = 'pid1'
        playlists = {
            'first': {'title': 'Uploader1 Videos', 'url': 'https://example.com/c1/videos'},
            playlist_id: {'title': 'Existing', 'url': 'https://example.com/p1'},
        }

        message = make_message(chat_id=123)
        cq = make_callback_query(data=playlist_id, message=message)
        update = make_update(callback_query=cq)
        context = make_context(
            state=state,
            user_data={'playlists': playlists},
        )

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, context)

        self.assertEqual(result, ConversationHandler.END)
        # edit_text is message.edit_text since we're in callback path
        message.edit_text.assert_awaited_once()
        text = message.edit_text.call_args[0][0]
        self.assertIn('Already subscribed', text)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_appends_chat_id(self, mock_get_ydl):
        existing_sub = Subscription(chat_ids=['999'], title='Existing', uploader='U', uploader_videos='v')
        state = make_state(subscriptions={'https://example.com/p1': existing_sub})

        playlist_id = 'pid1'
        playlists = {
            'first': {'title': 'U Videos', 'url': 'https://example.com/c1/videos'},
            playlist_id: {'title': 'Existing', 'url': 'https://example.com/p1'},
        }

        message = make_message(chat_id=123)
        cq = make_callback_query(data=playlist_id, message=message)
        update = make_update(callback_query=cq)
        context = make_context(
            state=state,
            user_data={'playlists': playlists},
        )

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, context)

        self.assertIn('123', existing_sub.chat_ids)
        self.assertIn('999', existing_sub.chat_ids)
        self.assertEqual(result, SUBSCRIBE_SHOW)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_message_path_with_uploader_videos(self, mock_get_ydl):
        ydl = MagicMock()
        ydl.extract_info.return_value = {
            'title': 'Channel',
            'uploader': 'Uploader1',
            'uploader_url': 'https://example.com/c1',
        }
        mock_get_ydl.return_value = ydl

        state = make_state()
        message = make_message(chat_id=123)
        update = make_update(message=message, callback_query=None)
        context = make_context(
            state=state,
            user_data={'uploader_videos': 'https://example.com/c1/videos'},
        )

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, context)

        self.assertIn('https://example.com/c1/videos', state.subscriptions)
        self.assertEqual(result, SUBSCRIBE_SHOW)


class TestUnsubscribe(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.unsubscribe_playlist')
    async def test_with_url_delegates(self, mock_unsub_playlist):
        mock_unsub_playlist.return_value = ConversationHandler.END
        message = make_message(text='/unsubscribe https://example.com/p1')
        update = make_update(message=message)
        state = make_state()
        context = make_context(state=state)

        from dasovbot.handlers.subscription import unsubscribe
        result = await unsubscribe(update, context)

        mock_unsub_playlist.assert_awaited_once()
        self.assertEqual(result, ConversationHandler.END)

    async def test_shows_buttons(self):
        subs = {
            'https://example.com/p1': Subscription(chat_ids=['123'], title='Sub1'),
        }
        state = make_state(subscriptions=subs)
        message = make_message(chat_id=123, text='/unsubscribe')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import unsubscribe
        result = await unsubscribe(update, context)

        message.reply_text.assert_awaited_once()
        self.assertEqual(result, UNSUBSCRIBE_PLAYLIST)
        self.assertIn('user_subscriptions', context.user_data)

    async def test_no_subs(self):
        state = make_state(subscriptions={})
        message = make_message(chat_id=123, text='/unsubscribe')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import unsubscribe
        result = await unsubscribe(update, context)

        message.reply_text.assert_awaited_once_with('No subscription found')
        self.assertEqual(result, ConversationHandler.END)


class TestUnsubscribePlaylist(unittest.IsolatedAsyncioTestCase):

    async def test_callback_cancel(self):
        message = make_message()
        cq = make_callback_query(data='cancel', message=message)
        update = make_update(callback_query=cq)
        context = make_context()

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, context)

        message.delete.assert_awaited_once()
        self.assertEqual(result, ConversationHandler.END)

    async def test_removes_chat_id(self):
        sub = Subscription(chat_ids=['123', '456'], title='Sub1')
        state = make_state(subscriptions={'https://example.com/p1': sub})

        sub_id = 'sid1'
        user_subs = {sub_id: {'title': 'Sub1', 'url': 'https://example.com/p1'}}

        message = make_message(chat_id=123)
        cq = make_callback_query(data=sub_id, message=message)
        update = make_update(callback_query=cq)
        context = make_context(
            state=state,
            user_data={'user_subscriptions': user_subs},
        )

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, context)

        self.assertNotIn('123', sub.chat_ids)
        self.assertIn('456', sub.chat_ids)
        self.assertIn('https://example.com/p1', state.subscriptions)
        self.assertEqual(result, ConversationHandler.END)

    async def test_last_subscriber_deletes(self):
        sub = Subscription(chat_ids=['123'], title='Sub1')
        state = make_state(subscriptions={'https://example.com/p1': sub})

        sub_id = 'sid1'
        user_subs = {sub_id: {'title': 'Sub1', 'url': 'https://example.com/p1'}}

        message = make_message(chat_id=123)
        cq = make_callback_query(data=sub_id, message=message)
        update = make_update(callback_query=cq)
        context = make_context(
            state=state,
            user_data={'user_subscriptions': user_subs},
        )

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, context)

        self.assertNotIn('https://example.com/p1', state.subscriptions)
        self.assertEqual(result, ConversationHandler.END)

    async def test_not_subscribed(self):
        sub = Subscription(chat_ids=['999'], title='Sub1')
        state = make_state(subscriptions={'https://example.com/p1': sub})

        sub_id = 'sid1'
        user_subs = {sub_id: {'title': 'Sub1', 'url': 'https://example.com/p1'}}

        message = make_message(chat_id=123)
        cq = make_callback_query(data=sub_id, message=message)
        update = make_update(callback_query=cq)
        context = make_context(
            state=state,
            user_data={'user_subscriptions': user_subs},
        )

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, context)

        message.edit_text.assert_awaited_once()
        text = message.edit_text.call_args[0][0]
        self.assertIn('No subscription found', text)
        self.assertEqual(result, ConversationHandler.END)

    async def test_invalid_url(self):
        state = make_state(subscriptions={})

        message = make_message(chat_id=123, text='/unsubscribe https://example.com/nonexistent')
        update = make_update(message=message, callback_query=None)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, context)

        message.reply_text.assert_awaited_once()
        text = message.reply_text.call_args[0][0]
        self.assertIn('Invalid selection', text)
        self.assertEqual(result, ConversationHandler.END)


class TestMultipleSubscribeUrls(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_empty_ends(self, mock_get_ydl):
        message = make_message(text='')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import multiple_subscribe_urls
        result = await multiple_subscribe_urls(update, context)

        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_multiple_success(self, mock_get_ydl):
        ydl = MagicMock()
        ydl.extract_info.side_effect = [
            {'title': 'C1', 'uploader': 'U1'},
            {'title': 'C2', 'uploader': 'U2'},
            {'title': 'C3', 'uploader': 'U3'},
        ]
        mock_get_ydl.return_value = ydl

        state = make_state()
        urls = 'https://example.com/c1\nhttps://example.com/c2\nhttps://example.com/c3'
        message = make_message(chat_id=123, text=urls)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import multiple_subscribe_urls
        result = await multiple_subscribe_urls(update, context)

        self.assertEqual(len(state.subscriptions), 3)
        # Last reply_text call contains summary
        last_call = message.reply_text.call_args_list[-1]
        self.assertIn('3 urls successfully', last_call[0][0])
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_already_subscribed(self, mock_get_ydl):
        existing_sub = Subscription(chat_ids=['123'], title='Existing')
        state = make_state(subscriptions={'https://example.com/c1': existing_sub})

        mock_get_ydl.return_value = MagicMock()

        message = make_message(chat_id=123, text='https://example.com/c1')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import multiple_subscribe_urls
        result = await multiple_subscribe_urls(update, context)

        # Should mention already subscribed
        calls = [call[0][0] for call in message.reply_text.call_args_list]
        combined = '\n'.join(calls)
        self.assertIn('already subscribed', combined.lower())
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_failed_urls(self, mock_get_ydl):
        ydl = MagicMock()
        ydl.extract_info.side_effect = [
            {'title': 'C1', 'uploader': 'U1'},
            Exception('not found'),
        ]
        mock_get_ydl.return_value = ydl

        state = make_state()
        urls = 'https://example.com/c1\nhttps://example.com/bad'
        message = make_message(chat_id=123, text=urls)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import multiple_subscribe_urls
        result = await multiple_subscribe_urls(update, context)

        calls = [call[0][0] for call in message.reply_text.call_args_list]
        combined = '\n'.join(calls)
        self.assertIn('failed', combined.lower())
        self.assertEqual(len(state.subscriptions), 1)
        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_appends_to_existing(self, mock_get_ydl):
        existing_sub = Subscription(chat_ids=['999'], title='Existing', uploader='U')
        state = make_state(subscriptions={'https://example.com/c1': existing_sub})

        mock_get_ydl.return_value = MagicMock()

        message = make_message(chat_id=123, text='https://example.com/c1')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import multiple_subscribe_urls
        result = await multiple_subscribe_urls(update, context)

        self.assertIn('123', existing_sub.chat_ids)
        self.assertIn('999', existing_sub.chat_ids)
        self.assertEqual(result, ConversationHandler.END)


if __name__ == '__main__':
    unittest.main()
