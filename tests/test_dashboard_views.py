import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import AioHTTPTestCase

from dasovbot.dashboard.server import create_app
from dasovbot.models import Intent, VideoInfo, Subscription, TemporaryInlineQuery
from tests.helpers import make_state, make_config


class DashboardViewTestCase(AioHTTPTestCase):
    def setUp(self):
        self._auth_patcher = patch('dasovbot.dashboard.auth.check_token', return_value=True)
        self._auth_patcher.start()
        self.addCleanup(self._auth_patcher.stop)
        super().setUp()

    async def get_application(self):
        self.state = make_state(
            config=make_config(),
            migration_progress={'status': 'skipped', 'tables': {}, 'elapsed': 0.0},
        )
        return create_app(self.state)


class TestIndex(DashboardViewTestCase):
    async def test_lists_active_intents_only(self):
        self.state.intents = {
            'https://example.com/one': Intent(priority=3, title='Video One', source='download'),
            'https://example.com/two': Intent(priority=1, title='Hidden Video', ignored=True),
        }
        resp = await self.client.get('/')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('Video One', text)
        self.assertNotIn('Hidden Video', text)


class TestVideos(DashboardViewTestCase):
    def _populate(self):
        self.state.videos = {
            'a': VideoInfo(title='Cat Video', file_id='f1', processed_at='20260101_000000', source='inline'),
            'b': VideoInfo(title='Dog Video', file_id='f2', processed_at='20260102_000000', source='download'),
            'c': VideoInfo(title='Pending Video'),
        }

    async def test_lists_only_uploaded_videos(self):
        self._populate()
        resp = await self.client.get('/videos')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('Cat Video', text)
        self.assertIn('Dog Video', text)
        self.assertNotIn('Pending Video', text)

    async def test_search_filters_by_title(self):
        self._populate()
        resp = await self.client.get('/videos?q=cat')
        text = await resp.text()
        self.assertIn('Cat Video', text)
        self.assertNotIn('Dog Video', text)

    async def test_source_filter(self):
        self._populate()
        resp = await self.client.get('/videos?source=download')
        text = await resp.text()
        self.assertIn('Dog Video', text)
        self.assertNotIn('Cat Video', text)

    async def test_invalid_page_defaults_to_first(self):
        self._populate()
        resp = await self.client.get('/videos?page=abc')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('Cat Video', text)

    async def test_sort_by_upload_date(self):
        self.state.videos = {
            'a': VideoInfo(title='Cat Video', file_id='f1', processed_at='20260101_000000', upload_date='20260310'),
            'b': VideoInfo(title='Dog Video', file_id='f2', processed_at='20260102_000000', upload_date='20260201'),
        }
        resp = await self.client.get('/videos?sort=upload_date')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertLess(text.index('Cat Video'), text.index('Dog Video'))
        # default sort (processed_at) orders them the other way around
        resp = await self.client.get('/videos')
        text = await resp.text()
        self.assertLess(text.index('Dog Video'), text.index('Cat Video'))


class TestIgnored(DashboardViewTestCase):
    async def test_lists_ignored_intents_and_inline_queries(self):
        self.state.intents = {
            'https://example.com/bad': Intent(ignored=True, title='Broken Video'),
            'https://example.com/ok': Intent(title='Fine Video'),
        }
        self.state.temporary_inline_queries = {
            'https://example.com/tiq': TemporaryInlineQuery(ignored=True),
        }
        resp = await self.client.get('/ignored')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('Broken Video', text)
        self.assertIn('https://example.com/tiq', text)
        self.assertNotIn('Fine Video', text)

    async def test_retry_ignored_intent(self):
        intent = Intent(ignored=True)
        self.state.intents = {'https://example.com/bad': intent}
        resp = await self.client.post(
            '/ignored/retry',
            data={'url': 'https://example.com/bad', 'type': 'intent'},
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 302)
        self.assertFalse(intent.ignored)
        self.assertFalse(self.state.download_queue.empty())

    async def test_retry_ignored_inline_query(self):
        tiq = TemporaryInlineQuery(ignored=True)
        self.state.temporary_inline_queries = {'https://example.com/tiq': tiq}
        resp = await self.client.post(
            '/ignored/retry',
            data={'url': 'https://example.com/tiq', 'type': 'inline'},
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 302)
        self.assertFalse(tiq.ignored)

    async def test_remove_ignored_intent(self):
        self.state.intents = {'https://example.com/bad': Intent(ignored=True)}
        resp = await self.client.post(
            '/ignored/remove',
            data={'url': 'https://example.com/bad', 'type': 'intent'},
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 302)
        self.assertNotIn('https://example.com/bad', self.state.intents)


class TestRemoveIntent(DashboardViewTestCase):
    async def test_removes_intent(self):
        self.state.intents = {'https://example.com/v': Intent()}
        resp = await self.client.post(
            '/intent/remove',
            data={'url': 'https://example.com/v'},
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 302)
        self.assertNotIn('https://example.com/v', self.state.intents)


class TestSubscriptions(DashboardViewTestCase):
    async def test_lists_subscriptions_with_user_labels(self):
        self.state.subscriptions = {
            'https://example.com/channel': Subscription(chat_ids=['1'], title='My Channel', uploader='Uploader'),
        }
        self.state.users = {'1': {'first_name': 'Ann'}}
        resp = await self.client.get('/subscriptions')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('My Channel', text)
        self.assertIn('Ann (1)', text)

    async def test_remove_last_subscriber_drops_subscription(self):
        self.state.subscriptions = {
            'https://example.com/channel': Subscription(chat_ids=['1'], title='My Channel'),
        }
        resp = await self.client.post(
            '/subscriptions/remove',
            data={'url': 'https://example.com/channel', 'chat_id': '1'},
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 302)
        self.assertNotIn('https://example.com/channel', self.state.subscriptions)

    async def test_remove_whole_subscription(self):
        self.state.subscriptions = {
            'https://example.com/channel': Subscription(chat_ids=['1', '2'], title='My Channel'),
        }
        resp = await self.client.post(
            '/subscriptions/remove',
            data={'url': 'https://example.com/channel'},
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 302)
        self.assertNotIn('https://example.com/channel', self.state.subscriptions)


class TestSystem(DashboardViewTestCase):
    async def test_shows_background_tasks(self):
        self.state.background_task_status = {'populate_subscriptions': '20260101_000000'}
        resp = await self.client.get('/system')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('populate_subscriptions', text)
        self.assertIn('monitor_process_intents', text)

    @patch('dasovbot.dashboard.views.run_populate_subscriptions', new_callable=AsyncMock)
    async def test_force_populate_redirects_to_system(self, mock_populate):
        resp = await self.client.post('/system/populate', allow_redirects=False)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers['Location'], '/system')
        await asyncio.sleep(0)
        mock_populate.assert_awaited_once_with(self.state)

    @patch('dasovbot.dashboard.views.run_populate_subscriptions', new_callable=AsyncMock)
    async def test_force_populate_redirects_to_index_from_index(self, mock_populate):
        resp = await self.client.post(
            '/system/populate',
            headers={'Referer': 'http://localhost/'},
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers['Location'], '/')


class TestIgnoredInlineTitle(DashboardViewTestCase):
    async def test_uses_title_from_cached_inline_results(self):
        from unittest.mock import MagicMock
        result = MagicMock()
        result.title = 'Cached Inline Title'
        self.state.temporary_inline_queries = {
            'https://example.com/tiq': TemporaryInlineQuery(ignored=True, results=[result]),
        }
        resp = await self.client.get('/ignored')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('Cached Inline Title', text)


class TestSubscriptionsUnknownUser(DashboardViewTestCase):
    async def test_label_falls_back_to_chat_id(self):
        self.state.subscriptions = {
            'https://example.com/channel': Subscription(chat_ids=['42'], title='My Channel'),
        }
        self.state.users = {}
        resp = await self.client.get('/subscriptions')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('42', text)
        self.assertNotIn('(42)', text)


class TestHealthAlerts(DashboardViewTestCase):
    async def test_no_banner_when_healthy(self):
        resp = await self.client.get('/')
        text = await resp.text()
        self.assertNotIn('class="alert', text)

    async def test_banner_rendered_on_every_page(self):
        self.state.set_alert('backup_stale', 'Backups may have stopped.', level='warning')
        for path in ('/', '/videos', '/system'):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 200)
            text = await resp.text()
            self.assertIn('Backups may have stopped.', text)
            self.assertIn('alert-warning', text)

    async def test_error_level_styling(self):
        self.state.set_alert('data_missing', 'Live database is empty.', level='error')
        resp = await self.client.get('/')
        text = await resp.text()
        self.assertIn('alert-error', text)
        self.assertIn('Live database is empty.', text)


if __name__ == '__main__':
    unittest.main()
