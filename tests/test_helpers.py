import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from dasovbot.helpers import (
    remove_command_prefix, user_subscriptions,
    send_message_developer, append_playlist,
)
from dasovbot.models import Subscription


class TestRemoveCommandPrefix(unittest.TestCase):
    def test_strips_command(self):
        self.assertEqual(remove_command_prefix('/download http://x.com'), 'http://x.com')

    def test_no_args(self):
        self.assertEqual(remove_command_prefix('/start'), '')

    def test_no_prefix(self):
        self.assertEqual(remove_command_prefix('http://x.com'), 'http://x.com')

    def test_whitespace(self):
        self.assertEqual(remove_command_prefix('/cmd   hello'), 'hello')

    def test_empty(self):
        self.assertEqual(remove_command_prefix(''), '')


class TestUserSubscriptions(unittest.TestCase):
    def test_filters_by_chat_id(self):
        subs = {
            'url1': Subscription(chat_ids=['1', '2'], title='A'),
            'url2': Subscription(chat_ids=['2', '3'], title='B'),
            'url3': Subscription(chat_ids=['1'], title='C'),
        }
        result = user_subscriptions('1', subs)
        titles = {v['title'] for v in result.values()}
        self.assertEqual(titles, {'A', 'C'})

    def test_no_match(self):
        subs = {'url1': Subscription(chat_ids=['2'], title='A')}
        result = user_subscriptions('99', subs)
        self.assertEqual(result, {})

    def test_empty_dict(self):
        result = user_subscriptions('1', {})
        self.assertEqual(result, {})

    def test_result_structure(self):
        subs = {'url1': Subscription(chat_ids=['1'], title='Title')}
        result = user_subscriptions('1', subs)
        self.assertEqual(len(result), 1)
        entry = list(result.values())[0]
        self.assertIn('title', entry)
        self.assertIn('url', entry)
        self.assertEqual(entry['title'], 'Title')
        self.assertEqual(entry['url'], 'url1')


class TestSendMessageDeveloper(unittest.IsolatedAsyncioTestCase):
    async def test_sends_message(self):
        bot = AsyncMock()
        await send_message_developer(bot, 'test text', '123')
        bot.send_message.assert_awaited_once_with(
            chat_id='123', text='test text', disable_notification=False
        )

    async def test_swallows_exceptions(self):
        bot = AsyncMock()
        bot.send_message.side_effect = Exception('network error')
        await send_message_developer(bot, 'text', '123')

    async def test_notification_flag(self):
        bot = AsyncMock()
        await send_message_developer(bot, 'text', '123', notification=False)
        bot.send_message.assert_awaited_once_with(
            chat_id='123', text='text', disable_notification=True
        )


class TestAppendPlaylist(unittest.TestCase):
    def test_adds_entry(self):
        playlists = {}
        append_playlist(playlists, 'My Playlist', 'http://example.com')
        self.assertEqual(len(playlists), 1)
        entry = list(playlists.values())[0]
        self.assertEqual(entry['title'], 'My Playlist')
        self.assertEqual(entry['url'], 'http://example.com')

    def test_multiple_entries_unique_keys(self):
        playlists = {}
        append_playlist(playlists, 'A', 'http://a.com')
        append_playlist(playlists, 'B', 'http://b.com')
        self.assertEqual(len(playlists), 2)
        keys = list(playlists.keys())
        self.assertNotEqual(keys[0], keys[1])


if __name__ == '__main__':
    unittest.main()
