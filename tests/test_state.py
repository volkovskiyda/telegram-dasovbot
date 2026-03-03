import unittest
from unittest.mock import AsyncMock, patch

from tests.helpers import make_state
from dasovbot.models import VideoInfo, Intent, IntentMessage, Subscription


class TestSetVideo(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.upsert_video', new_callable=AsyncMock)
    async def test_stores_in_memory_and_calls_db(self, mock_upsert):
        state = make_state()
        video = VideoInfo(title='Test')
        await state.set_video('k1', video)
        self.assertIs(state.videos['k1'], video)
        mock_upsert.assert_awaited_once_with(state.db, 'k1', video)


class TestSetIntent(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_stores_in_memory_and_calls_db(self, mock_upsert):
        state = make_state()
        intent = Intent(chat_ids=['1'])
        await state.set_intent('q', intent)
        self.assertIs(state.intents['q'], intent)
        mock_upsert.assert_awaited_once_with(state.db, 'q', intent)


class TestSaveIntent(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_saves_existing(self, mock_upsert):
        intent = Intent(priority=5)
        state = make_state(intents={'q': intent})
        await state.save_intent('q')
        mock_upsert.assert_awaited_once_with(state.db, 'q', intent)

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_noop_for_missing(self, mock_upsert):
        state = make_state()
        await state.save_intent('missing')
        mock_upsert.assert_not_awaited()


class TestPopIntent(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_removes_from_memory_and_db(self, mock_delete):
        intent = Intent(chat_ids=['1'])
        state = make_state(intents={'q': intent})
        result = await state.pop_intent('q')
        self.assertIs(result, intent)
        self.assertNotIn('q', state.intents)
        mock_delete.assert_awaited_once_with(state.db, 'q')

    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_returns_none_for_missing(self, mock_delete):
        state = make_state()
        result = await state.pop_intent('nope')
        self.assertIsNone(result)
        mock_delete.assert_awaited_once_with(state.db, 'nope')


class TestSetUser(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.upsert_user', new_callable=AsyncMock)
    async def test_stores_in_memory_and_calls_db(self, mock_upsert):
        state = make_state()
        data = {'name': 'Bob'}
        await state.set_user('42', data)
        self.assertEqual(state.users['42'], data)
        mock_upsert.assert_awaited_once_with(state.db, '42', data)


class TestSetSubscription(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.upsert_subscription', new_callable=AsyncMock)
    async def test_stores_in_memory_and_calls_db(self, mock_upsert):
        state = make_state()
        sub = Subscription(title='Ch')
        await state.set_subscription('url', sub)
        self.assertIs(state.subscriptions['url'], sub)
        mock_upsert.assert_awaited_once_with(state.db, 'url', sub)


class TestPopSubscription(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.delete_subscription', new_callable=AsyncMock)
    async def test_removes_and_returns(self, mock_delete):
        sub = Subscription(title='Ch')
        state = make_state(subscriptions={'url': sub})
        result = await state.pop_subscription('url')
        self.assertIs(result, sub)
        self.assertNotIn('url', state.subscriptions)
        mock_delete.assert_awaited_once_with(state.db, 'url')

    @patch('dasovbot.database.delete_subscription', new_callable=AsyncMock)
    async def test_returns_none_for_missing(self, mock_delete):
        state = make_state()
        result = await state.pop_subscription('nope')
        self.assertIsNone(result)


class TestAddSubscriber(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.upsert_subscription', new_callable=AsyncMock)
    async def test_appends_chat_id(self, mock_upsert):
        sub = Subscription(chat_ids=['1'])
        state = make_state(subscriptions={'url': sub})
        await state.add_subscriber('url', '2')
        self.assertEqual(sub.chat_ids, ['1', '2'])
        mock_upsert.assert_awaited_once()

    @patch('dasovbot.database.upsert_subscription', new_callable=AsyncMock)
    async def test_deduplicates(self, mock_upsert):
        sub = Subscription(chat_ids=['1'])
        state = make_state(subscriptions={'url': sub})
        await state.add_subscriber('url', '1')
        self.assertEqual(sub.chat_ids, ['1'])
        mock_upsert.assert_not_awaited()

    @patch('dasovbot.database.upsert_subscription', new_callable=AsyncMock)
    async def test_noop_if_missing(self, mock_upsert):
        state = make_state()
        await state.add_subscriber('nope', '1')
        mock_upsert.assert_not_awaited()


class TestRemoveSubscriber(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.database.delete_subscription', new_callable=AsyncMock)
    @patch('dasovbot.database.upsert_subscription', new_callable=AsyncMock)
    async def test_removes_chat_id(self, mock_upsert, mock_delete):
        sub = Subscription(chat_ids=['1', '2'])
        state = make_state(subscriptions={'url': sub})
        await state.remove_subscriber('url', '1')
        self.assertEqual(sub.chat_ids, ['2'])
        mock_upsert.assert_awaited_once()
        mock_delete.assert_not_awaited()

    @patch('dasovbot.database.delete_subscription', new_callable=AsyncMock)
    @patch('dasovbot.database.upsert_subscription', new_callable=AsyncMock)
    async def test_deletes_subscription_when_last_removed(self, mock_upsert, mock_delete):
        sub = Subscription(chat_ids=['1'])
        state = make_state(subscriptions={'url': sub})
        await state.remove_subscriber('url', '1')
        self.assertNotIn('url', state.subscriptions)
        mock_delete.assert_awaited_once_with(state.db, 'url')
        mock_upsert.assert_not_awaited()

    @patch('dasovbot.database.delete_subscription', new_callable=AsyncMock)
    @patch('dasovbot.database.upsert_subscription', new_callable=AsyncMock)
    async def test_noop_if_missing(self, mock_upsert, mock_delete):
        state = make_state()
        await state.remove_subscriber('nope', '1')
        mock_upsert.assert_not_awaited()
        mock_delete.assert_not_awaited()


class TestClose(unittest.IsolatedAsyncioTestCase):
    async def test_closes_db(self):
        state = make_state()
        await state.close()
        state.db.close.assert_awaited_once()

    async def test_noop_without_db(self):
        state = make_state(db=None)
        await state.close()


if __name__ == '__main__':
    unittest.main()
