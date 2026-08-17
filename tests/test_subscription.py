import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ConversationHandler

from dasovbot.constants import (
    SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW,
    UNSUBSCRIBE_PLAYLIST, MULTIPLE_SUBSCRIBE_URLS,
)
from dasovbot.handlers.subscription import (
    _button_size, _paginate_items, build_paginated_keyboard, BUTTON_OVERHEAD,
)
from dasovbot.models import Subscription, VideoInfo
from tests.helpers import (
    make_message, make_callback_query,
    make_update, make_context, make_state,
)


class TestButtonSize(unittest.TestCase):
    def test_counts_utf8_bytes_plus_overhead(self):
        self.assertEqual(_button_size('ab', 'cd'), 4 + BUTTON_OVERHEAD)

    def test_multibyte_titles_count_encoded_length(self):
        self.assertEqual(_button_size('é', ''), 2 + BUTTON_OVERHEAD)


class TestPaginateItems(unittest.TestCase):
    def _items(self, titles):
        return [(f'id{i}', {'title': title, 'url': f'u{i}'}) for i, title in enumerate(titles)]

    def test_empty_returns_single_empty_page(self):
        self.assertEqual(_paginate_items([]), [[]])

    def test_single_page_when_under_budget(self):
        items = self._items(['a', 'bb', 'ccc'])
        pages = _paginate_items(items)
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]), 3)

    def test_splits_pages_when_over_budget(self):
        items = self._items(['x' * 1000 for _ in range(4)])
        pages = _paginate_items(items)
        self.assertGreater(len(pages), 1)
        flattened = [item for page in pages for item in page]
        self.assertEqual(sorted(id for id, _ in flattened), sorted(id for id, _ in items))

    def test_sorts_by_title_length(self):
        items = self._items(['long title here', 'a', 'medium'])
        pages = _paginate_items(items)
        titles = [data['title'] for _, data in pages[0]]
        self.assertEqual(titles, ['a', 'medium', 'long title here'])


class TestBuildPaginatedKeyboard(unittest.TestCase):
    def test_single_page_has_only_cancel_footer(self):
        items = {'id1': {'title': 'One', 'url': 'u1'}, 'id2': {'title': 'Two', 'url': 'u2'}}
        keyboard = build_paginated_keyboard(items, 0)
        self.assertEqual(keyboard[-1][0].callback_data, 'cancel')
        callbacks = [button.callback_data for row in keyboard for button in row]
        self.assertFalse(any(cb.startswith('page:') for cb in callbacks))
        self.assertNotIn('noop', callbacks)

    def _multi_page_items(self):
        return {f'id{i}': {'title': 'x' * 1000, 'url': f'u{i}'} for i in range(6)}

    def test_first_page_nav_has_next_and_indicator(self):
        keyboard = build_paginated_keyboard(self._multi_page_items(), 0)
        nav_row = keyboard[-2]
        callbacks = [button.callback_data for button in nav_row]
        self.assertIn('noop', callbacks)
        self.assertIn('page:1', callbacks)
        self.assertFalse(any(button.text == '< Prev' for button in nav_row))
        indicator = next(button for button in nav_row if button.callback_data == 'noop')
        self.assertTrue(indicator.text.startswith('1/'))

    def test_last_page_nav_has_prev_only(self):
        items = self._multi_page_items()
        total_pages = len(_paginate_items(list(items.items())))
        keyboard = build_paginated_keyboard(items, total_pages - 1)
        nav_row = keyboard[-2]
        self.assertTrue(any(button.text == '< Prev' for button in nav_row))
        self.assertFalse(any(button.text == 'Next >' for button in nav_row))

    def test_page_clamped_to_valid_range(self):
        items = self._multi_page_items()
        total_pages = len(_paginate_items(list(items.items())))
        clamped = build_paginated_keyboard(items, 99)
        last = build_paginated_keyboard(items, total_pages - 1)
        self.assertEqual(
            [[button.callback_data for button in row] for row in clamped],
            [[button.callback_data for button in row] for row in last],
        )


class TestSubscribeShow(unittest.IsolatedAsyncioTestCase):
    def _make_update(self, answer: str, text='Subscribed to [T](url)\nShow latest videos?'):
        message = make_message(chat_id=123, text=text)
        cq = make_callback_query(data=answer, message=message)
        return make_update(callback_query=cq), message, cq

    @patch('dasovbot.handlers.subscription.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_yes_sends_cached_and_enqueues_uncached(self, mock_get_ydl, mock_append):
        ydl = MagicMock()
        ydl.extract_info.return_value = {'entries': [
            {'url': 'https://example.com/v1', 'webpage_url': 'https://example.com/v1'},
            {'url': 'https://example.com/v2', 'webpage_url': 'https://example.com/v2'},
        ]}
        mock_get_ydl.return_value = ydl
        cached = VideoInfo(title='V1', file_id='fid1', caption='cap1')
        state = make_state(videos={'https://example.com/v1': cached})
        update, message, cq = self._make_update('True')
        context = make_context(state=state, user_data={'subscription_url': 'https://example.com/c/videos'})

        from dasovbot.handlers.subscription import subscribe_show
        result = await subscribe_show(update, context)

        self.assertEqual(result, ConversationHandler.END)
        cq.answer.assert_awaited_once()
        # The cached video is sent immediately...
        context.bot.send_video.assert_awaited_once_with(123, 'fid1', caption='cap1')
        # ...and the uncached one is enqueued rather than silently skipped.
        mock_append.assert_awaited_once()
        self.assertEqual(mock_append.call_args[0][0], 'https://example.com/v2')
        self.assertNotIn('subscription_url', context.user_data)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_no_answer_skips_lookup(self, mock_get_ydl):
        update, message, cq = self._make_update('False')
        context = make_context(user_data={'subscription_url': 'https://example.com/c/videos'})

        from dasovbot.handlers.subscription import subscribe_show
        result = await subscribe_show(update, context)

        self.assertEqual(result, ConversationHandler.END)
        mock_get_ydl.assert_not_called()
        context.bot.send_video.assert_not_awaited()

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_extraction_error_swallowed(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.side_effect = Exception('network error')
        update, message, cq = self._make_update('True')
        context = make_context(user_data={'subscription_url': 'https://example.com/c/videos'})

        from dasovbot.handlers.subscription import subscribe_show
        result = await subscribe_show(update, context)

        self.assertEqual(result, ConversationHandler.END)
        message.edit_text.assert_awaited_once()
        context.bot.send_video.assert_not_awaited()


class TestPlaylists(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_no_subscriptions_replies(self, mock_get_ydl):
        message = make_message(chat_id=123)
        update = make_update(message=message)
        context = make_context(state=make_state())

        from dasovbot.handlers.subscription import playlists
        result = await playlists(update, context)

        self.assertEqual(result, ConversationHandler.END)
        message.reply_text.assert_awaited_once_with('No subscriptions')

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_suggests_streams_for_videos_subscriber(self, mock_get_ydl):
        sub = Subscription(chat_ids=['123'], title='C1', uploader='u1',
                           uploader_videos='https://example.com/c1/videos')
        state = make_state(subscriptions={'https://example.com/c1/videos': sub})
        ydl = MagicMock()
        ydl.extract_info.return_value = {'uploader_url': 'https://example.com/c1'}
        mock_get_ydl.return_value = ydl
        message = make_message(chat_id=123)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import playlists
        result = await playlists(update, context)

        self.assertEqual(result, ConversationHandler.END)
        message.reply_text.assert_awaited_once()
        reply = message.reply_text.await_args.args[0]
        self.assertIn('Available Streams', reply)
        self.assertIn('https://example.com/c1/streams', reply)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_both_subscriptions_grouped(self, mock_get_ydl):
        videos_sub = Subscription(chat_ids=['123'], title='C1 Videos', uploader='u1',
                                  uploader_videos='https://example.com/c1/videos')
        streams_sub = Subscription(chat_ids=['123'], title='C1 Streams', uploader='u1',
                                   uploader_videos='https://example.com/c1/videos')
        state = make_state(subscriptions={
            'https://example.com/c1/videos': videos_sub,
            'https://example.com/c1/streams': streams_sub,
        })
        ydl = MagicMock()
        ydl.extract_info.return_value = {'uploader_url': 'https://example.com/c1'}
        mock_get_ydl.return_value = ydl
        message = make_message(chat_id=123)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import playlists
        result = await playlists(update, context)

        self.assertEqual(result, ConversationHandler.END)
        message.reply_text.assert_awaited_once()
        reply = message.reply_text.await_args.args[0]
        self.assertIn('Videos and Streams', reply)
        self.assertEqual(reply.count('https://example.com/c1'), 1)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_extraction_error_skipped(self, mock_get_ydl):
        sub = Subscription(chat_ids=['123'], title='C1', uploader='u1',
                           uploader_videos='https://example.com/c1/videos')
        state = make_state(subscriptions={'https://example.com/c1/videos': sub})
        mock_get_ydl.return_value.extract_info.side_effect = Exception('network error')
        message = make_message(chat_id=123)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import playlists
        result = await playlists(update, context)

        self.assertEqual(result, ConversationHandler.END)
        message.reply_text.assert_not_awaited()


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

    async def test_long_list_chunked_into_multiple_replies(self):
        # An oversized single reply used to raise inside `except: pass`,
        # leaving the user with no response at all
        subs = {
            f'https://example.com/c{i}/videos': Subscription(chat_ids=['123'], title=f'Channel {i} {"x" * 200}')
            for i in range(40)
        }
        state = make_state(subscriptions=subs)
        message = make_message(chat_id=123)
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import subscription_list
        await subscription_list(update, context)

        self.assertGreater(message.reply_markdown.await_count, 1)
        for call in message.reply_markdown.call_args_list:
            self.assertLessEqual(len(call[0][0]), 4096)

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


class TestSubscribePlaylistStaleCallback(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_stale_selection_replies_invalid(self, mock_get_ydl):
        state = make_state()
        playlists = {'id1': {'title': 'T', 'url': 'https://example.com/p1'}}
        message = make_message(chat_id=123)
        cq = make_callback_query(data='stale-id', message=message)
        update = make_update(callback_query=cq)
        context = make_context(state=state, user_data={'playlists': playlists})

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, context)

        self.assertEqual(result, ConversationHandler.END)
        message.edit_text.assert_awaited_once()
        self.assertIn('Invalid selection', message.edit_text.await_args.args[0])
        self.assertEqual(state.subscriptions, {})

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_page_nav_without_playlists_errors(self, mock_get_ydl):
        state = make_state()
        message = make_message(chat_id=123)
        cq = make_callback_query(data='page:1', message=message)
        update = make_update(callback_query=cq)
        context = make_context(state=state, user_data={})

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, context)

        self.assertEqual(result, ConversationHandler.END)
        message.edit_text.assert_awaited_once()
        self.assertIn('Error occurred', message.edit_text.await_args.args[0])


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

    async def test_stale_selection_replies_invalid(self):
        sub = Subscription(chat_ids=['123'], title='Sub1')
        state = make_state(subscriptions={'https://example.com/c1/videos': sub})
        user_subs = {'id1': {'title': 'Sub1', 'url': 'https://example.com/c1/videos'}}
        message = make_message(chat_id=123)
        cq = make_callback_query(data='stale-id', message=message)
        update = make_update(callback_query=cq)
        context = make_context(state=state, user_data={'user_subscriptions': user_subs})

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, context)

        self.assertEqual(result, ConversationHandler.END)
        message.edit_text.assert_awaited_once()
        self.assertIn('Invalid selection', message.edit_text.await_args.args[0])
        self.assertEqual(sub.chat_ids, ['123'])

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
    async def test_reply_failure_still_ends_conversation(self, mock_get_ydl):
        # A raising reply used to abort before returning END, leaving the
        # conversation stuck in MULTIPLE_SUBSCRIBE_URLS re-parsing messages as URLs
        existing_sub = Subscription(chat_ids=['123'], title='Existing')
        state = make_state(subscriptions={'https://example.com/c1': existing_sub})
        mock_get_ydl.return_value = MagicMock()
        message = make_message(chat_id=123, text='https://example.com/c1')
        message.reply_text.side_effect = Exception('Message is too long')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import multiple_subscribe_urls
        result = await multiple_subscribe_urls(update, context)

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


class TestUnsubscribePlaylistNavigation(unittest.IsolatedAsyncioTestCase):

    async def test_noop_keeps_state_and_subscriptions(self):
        user_subs = {'id1': {'title': 'Sub1', 'url': 'https://example.com/c1/videos'}}
        message = make_message(chat_id=123)
        cq = make_callback_query(data='noop', message=message)
        update = make_update(callback_query=cq)
        context = make_context(user_data={'user_subscriptions': user_subs})

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, context)

        self.assertEqual(result, UNSUBSCRIBE_PLAYLIST)
        message.edit_text.assert_not_awaited()
        self.assertEqual(context.user_data['user_subscriptions'], user_subs)

    async def test_page_navigation_rebuilds_keyboard(self):
        user_subs = {'id1': {'title': 'Sub1', 'url': 'https://example.com/c1/videos'}}
        message = make_message(chat_id=123)
        cq = make_callback_query(data='page:0', message=message)
        update = make_update(callback_query=cq)
        context = make_context(user_data={'user_subscriptions': user_subs})

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, context)

        self.assertEqual(result, UNSUBSCRIBE_PLAYLIST)
        message.edit_reply_markup.assert_awaited_once()
        self.assertEqual(context.user_data['user_subscriptions'], user_subs)

    async def test_cancel_delete_failure_still_ends(self):
        message = make_message()
        message.delete.side_effect = Exception('already deleted')
        cq = make_callback_query(data='cancel', message=message)
        update = make_update(callback_query=cq)

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, make_context())

        self.assertEqual(result, ConversationHandler.END)


class TestSubscribeUrlBranches(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_playlists_without_entries_replies_error(self, mock_get_ydl):
        uploader_url = 'https://example.com/c'

        def extract(url, download=False):
            if url == uploader_url:
                return {'uploader_url': uploader_url}
            if url == f'{uploader_url}/playlists':
                return {'entries': None, 'uploader': 'Uploader'}
            raise ValueError(url)

        mock_get_ydl.return_value.extract_info.side_effect = extract
        message = make_message(chat_id=123, text=f'/subscribe {uploader_url}')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import subscribe_url
        result = await subscribe_url(update, make_context())

        self.assertEqual(result, ConversationHandler.END)
        self.assertIn('Error occurred', message.reply_text.await_args.args[0])

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_streams_playlist_offered_when_available(self, mock_get_ydl):
        uploader_url = 'https://example.com/c'

        def extract(url, download=False):
            if url == uploader_url:
                return {'uploader_url': uploader_url}
            if url == f'{uploader_url}/playlists':
                return {'entries': [{'title': 'P1', 'url': f'{uploader_url}/p1'}], 'uploader': 'Uploader'}
            if url == f'{uploader_url}/streams':
                return {}
            raise ValueError(url)

        mock_get_ydl.return_value.extract_info.side_effect = extract
        message = make_message(chat_id=123, text=f'/subscribe {uploader_url}')
        update = make_update(message=message)
        context = make_context()

        from dasovbot.handlers.subscription import subscribe_url
        result = await subscribe_url(update, context)

        self.assertEqual(result, SUBSCRIBE_PLAYLIST)
        titles = [item['title'] for item in context.user_data['playlists'].values()]
        self.assertIn('Uploader Streams', titles)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_query_matching_playlist_subscribes_directly(self, mock_get_ydl):
        uploader_url = 'https://example.com/c'
        query = f'{uploader_url}/p1'

        def extract(url, download=False):
            if url == query:
                return {'uploader_url': uploader_url, 'title': 'P1', 'uploader': 'Uploader'}
            if url == uploader_url:
                return {}
            if url == f'{uploader_url}/playlists':
                return {'entries': [{'title': 'P1', 'url': query}], 'uploader': 'Uploader'}
            if url == f'{uploader_url}/streams':
                raise ValueError('no streams')
            raise ValueError(url)

        mock_get_ydl.return_value.extract_info.side_effect = extract
        state = make_state()
        message = make_message(chat_id=123, text=f'/subscribe {query}')
        update = make_update(message=message)
        context = make_context(state=state)

        from dasovbot.handlers.subscription import subscribe_url
        result = await subscribe_url(update, context)

        self.assertEqual(result, SUBSCRIBE_SHOW)
        self.assertIn(query, state.subscriptions)


class TestSubscribePlaylistBranches(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_cancel_delete_failure_still_ends(self, mock_get_ydl):
        message = make_message()
        message.delete.side_effect = Exception('already deleted')
        cq = make_callback_query(data='cancel', message=message)
        update = make_update(callback_query=cq)

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, make_context())

        self.assertEqual(result, ConversationHandler.END)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_noop_stays_in_state(self, mock_get_ydl):
        cq = make_callback_query(data='noop', message=make_message())
        update = make_update(callback_query=cq)

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, make_context())

        self.assertEqual(result, SUBSCRIBE_PLAYLIST)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_noop_keeps_playlists_for_next_selection(self, mock_get_ydl):
        playlists = {'id1': {'title': 'P1', 'url': 'https://example.com/p1'}}
        state = make_state()
        context = make_context(state=state, user_data={'playlists': playlists})
        message = make_message(chat_id=123)

        from dasovbot.handlers.subscription import subscribe_playlist

        noop_cq = make_callback_query(data='noop', message=message)
        result = await subscribe_playlist(make_update(callback_query=noop_cq), context)
        self.assertEqual(result, SUBSCRIBE_PLAYLIST)
        self.assertEqual(context.user_data['playlists'], playlists)

        select_cq = make_callback_query(data='id1', message=message)
        result = await subscribe_playlist(make_update(callback_query=select_cq), context)
        self.assertEqual(result, SUBSCRIBE_SHOW)
        self.assertIn('https://example.com/p1', state.subscriptions)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_cancel_clears_stored_playlists(self, mock_get_ydl):
        context = make_context(user_data={'playlists': {'id1': {'title': 'P1', 'url': 'u'}}})
        cq = make_callback_query(data='cancel', message=make_message())

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(make_update(callback_query=cq), context)

        self.assertEqual(result, ConversationHandler.END)
        self.assertNotIn('playlists', context.user_data)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_page_navigation_rebuilds_keyboard(self, mock_get_ydl):
        playlists = {'id1': {'title': 'P1', 'url': 'https://example.com/p1'}}
        message = make_message()
        cq = make_callback_query(data='page:0', message=message)
        update = make_update(callback_query=cq)
        context = make_context(user_data={'playlists': playlists})

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, context)

        self.assertEqual(result, SUBSCRIBE_PLAYLIST)
        message.edit_reply_markup.assert_awaited_once()
        self.assertEqual(context.user_data['playlists'], playlists)

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_message_without_url_replies_invalid(self, mock_get_ydl):
        message = make_message(chat_id=123, text='/subscribe')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, make_context())

        self.assertEqual(result, ConversationHandler.END)
        self.assertIn('Invalid selection', message.reply_text.await_args.args[0])

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_direct_url_without_title_falls_back_to_uploader(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.return_value = {
            'uploader_url': 'https://example.com/c', 'title': None, 'uploader': 'Uploader',
        }
        state = make_state()
        message = make_message(chat_id=123, text='/subscribe https://example.com/p1')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, make_context(state=state))

        self.assertEqual(result, SUBSCRIBE_SHOW)
        self.assertEqual(state.subscriptions['https://example.com/p1'].title, 'Uploader')

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_direct_url_extract_failure_replies_error(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.side_effect = ValueError('boom')
        state = make_state()
        message = make_message(chat_id=123, text='/subscribe https://example.com/p1')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import subscribe_playlist
        result = await subscribe_playlist(update, make_context(state=state))

        self.assertEqual(result, ConversationHandler.END)
        self.assertIn('Error occurred', message.reply_text.await_args.args[0])
        self.assertEqual(state.subscriptions, {})


class TestUnsubscribePlaylistNoState(unittest.IsolatedAsyncioTestCase):

    async def test_selection_without_stored_subs_replies_error(self):
        message = make_message(chat_id=123)
        cq = make_callback_query(data='some-id', message=message)
        update = make_update(callback_query=cq)

        from dasovbot.handlers.subscription import unsubscribe_playlist
        result = await unsubscribe_playlist(update, make_context(user_data={}))

        self.assertEqual(result, ConversationHandler.END)
        self.assertIn('Error occurred', message.edit_text.await_args.args[0])


class TestPlaylistsBranches(unittest.IsolatedAsyncioTestCase):

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_streams_subscriber_offered_videos(self, mock_get_ydl):
        base = 'https://example.com/c'
        sub = Subscription(chat_ids=['123'], title='Streams')
        no_uploader = Subscription(chat_ids=['123'], title='Odd')
        state = make_state(subscriptions={
            f'{base}/streams': sub,
            'https://example.com/no-uploader': no_uploader,
        })

        def extract(url, download=False):
            if url == f'{base}/streams':
                return {'uploader_url': base}
            if url == f'{base}/videos':
                return {}
            if url == 'https://example.com/no-uploader':
                return {}
            raise ValueError(url)

        mock_get_ydl.return_value.extract_info.side_effect = extract
        message = make_message(chat_id=123)
        update = make_update(message=message)

        from dasovbot.handlers.subscription import playlists
        result = await playlists(update, make_context(state=state))

        self.assertEqual(result, ConversationHandler.END)
        replies = [call.args[0] for call in message.reply_text.await_args_list]
        self.assertTrue(any('Available Videos' in reply for reply in replies))

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_probe_failures_suggest_nothing(self, mock_get_ydl):
        base_a = 'https://example.com/a'
        base_b = 'https://example.com/b'
        state = make_state(subscriptions={
            f'{base_a}/videos': Subscription(chat_ids=['123'], title='Videos'),
            f'{base_b}/streams': Subscription(chat_ids=['123'], title='Streams'),
        })

        def extract(url, download=False):
            if url == f'{base_a}/videos':
                return {'uploader_url': base_a}
            if url == f'{base_b}/streams':
                return {'uploader_url': base_b}
            raise ValueError(url)  # both probes fail

        mock_get_ydl.return_value.extract_info.side_effect = extract
        message = make_message(chat_id=123)
        update = make_update(message=message)

        from dasovbot.handlers.subscription import playlists
        result = await playlists(update, make_context(state=state))

        self.assertEqual(result, ConversationHandler.END)
        message.reply_text.assert_not_awaited()

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_no_subscriptions_reply_failure_swallowed(self, mock_get_ydl):
        message = make_message(chat_id=123)
        message.reply_text.side_effect = Exception('blocked')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import playlists
        result = await playlists(update, make_context(state=make_state()))

        self.assertEqual(result, ConversationHandler.END)


class TestSubscriptionListErrors(unittest.IsolatedAsyncioTestCase):

    async def test_reply_failure_swallowed(self):
        sub = Subscription(chat_ids=['123'], title='Sub1')
        state = make_state(subscriptions={'https://example.com/c1/videos': sub})
        message = make_message(chat_id=123)
        message.reply_markdown.side_effect = Exception('blocked')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import subscription_list
        await subscription_list(update, make_context(state=state))  # must not raise


class TestMultipleSubscribe(unittest.IsolatedAsyncioTestCase):

    async def test_prompts_for_urls(self):
        message = make_message(text='/multiple_subscribe')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import multiple_subscribe
        result = await multiple_subscribe(update, None)

        self.assertEqual(result, MULTIPLE_SUBSCRIBE_URLS)
        message.reply_text.assert_awaited_once_with('Enter urls')

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_null_title_falls_back_to_url(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.return_value = {'title': None, 'uploader': None}
        state = make_state()
        message = make_message(chat_id=123, text='https://example.com/a')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import multiple_subscribe_urls
        await multiple_subscribe_urls(update, make_context(state=state))

        self.assertEqual(state.subscriptions['https://example.com/a'].title, 'https://example.com/a')

    @patch('dasovbot.handlers.subscription.get_ydl')
    async def test_blank_lines_counted_as_failed(self, mock_get_ydl):
        mock_get_ydl.return_value.extract_info.return_value = {
            'title': 'T', 'uploader': 'U',
        }
        state = make_state()
        message = make_message(chat_id=123, text='https://example.com/a\n\nhttps://example.com/b')
        update = make_update(message=message)

        from dasovbot.handlers.subscription import multiple_subscribe_urls
        result = await multiple_subscribe_urls(update, make_context(state=state))

        self.assertEqual(result, ConversationHandler.END)
        self.assertIn('https://example.com/a', state.subscriptions)
        self.assertIn('https://example.com/b', state.subscriptions)
        replies = [call.args[0] for call in message.reply_text.await_args_list]
        self.assertTrue(any('Failed subscriptions' in reply for reply in replies))


if __name__ == '__main__':
    unittest.main()
