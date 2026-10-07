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


class TestUsersAndBan(DashboardViewTestCase):
    def _populate(self):
        self.state.videos = {
            'https://youtu.be/a': VideoInfo(title='Cat Video', file_id='f1', webpage_url='https://www.youtube.com/watch?v=a'),
            'https://www.youtube.com/watch?v=b': VideoInfo(title='Dog Video', file_id='f2'),
        }
        self.state.users = {'7': {'first_name': 'Ann', 'username': 'ann7'}}
        # Logged under the canonical URL, shown on the alternate-key row too
        self.state.video_requesters = {'https://www.youtube.com/watch?v=a': ['7', '8']}
        self.state.user_requests = {
            '7': {'count': 5, 'last_at': '20260101_000000'},
            '8': {'count': 1, 'last_at': '20260102_000000'},
        }

    async def test_videos_show_requesters_with_ban_buttons(self):
        self._populate()
        resp = await self.client.get('/videos')
        text = await resp.text()
        self.assertIn('Ann @ann7 (7)', text)
        self.assertIn('action="/users/ban"', text)
        self.assertIn('<th>User</th>', text)

    async def test_videos_search_matches_requester(self):
        self._populate()
        resp = await self.client.get('/videos?q=ann7')
        text = await resp.text()
        self.assertIn('Cat Video', text)
        self.assertNotIn('Dog Video', text)

    async def test_videos_user_filter_matches_requester_id_exactly(self):
        self._populate()
        # '7' is a substring of '77', and of this video's id: neither may match
        self.state.videos['https://www.youtube.com/watch?v=b77'] = VideoInfo(title='Bird 7 Video', file_id='f3')
        self.state.video_requesters['https://www.youtube.com/watch?v=b77'] = ['77']
        resp = await self.client.get('/videos?user=7')
        text = await resp.text()
        self.assertIn('Cat Video', text)
        self.assertNotIn('Dog Video', text)
        self.assertNotIn('Bird 7 Video', text)
        self.assertIn('Requested by:', text)
        self.assertIn('Ann @ann7 (7)', text)
        # The filter survives the sort, source, pager and search links
        self.assertIn('sort=upload_date&source=all&q=&user=7', text)
        self.assertIn('name="user" value="7"', text)

    async def test_user_links_filter_by_id(self):
        self._populate()
        for path in ['/videos', '/users']:
            text = await (await self.client.get(path)).text()
            self.assertIn('href="/videos?user=7"', text)
            self.assertNotIn('href="/videos?q=7"', text)

    async def test_users_page_sorted_by_request_count(self):
        self._populate()
        self.state.banned_users = {'9': {'banned_at': '20260103_000000', 'name': 'Zed'}}
        resp = await self.client.get('/users')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertLess(text.index('Ann @ann7 (7)'), text.index('>8<'))
        self.assertIn('Zed (9)', text)
        self.assertIn('action="/users/unban"', text)

    @patch('dasovbot.database.upsert_banned_user', new_callable=AsyncMock)
    async def test_ban_stores_name_and_redirects_back(self, mock_upsert):
        self._populate()
        resp = await self.client.post(
            '/users/ban',
            data={'user_id': '7', 'next': '/videos?page=2'},
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers['Location'], '/videos?page=2')
        self.assertTrue(self.state.is_banned('7'))
        self.assertEqual(self.state.banned_users['7']['name'], 'Ann @ann7')

    def test_safe_next_rejects_everything_but_local_paths(self):
        from dasovbot.dashboard.views import safe_next
        for target in ['//evil.example', '/\\evil.example', '/\t/evil.example', '/\n/evil.example',
                       'https://evil.example', 'videos', '']:
            self.assertEqual(safe_next(target, '/users'), '/users', repr(target))
        self.assertEqual(safe_next('/videos?page=2&q=a%20b', '/users'), '/videos?page=2&q=a%20b')

    @patch('dasovbot.database.upsert_banned_user', new_callable=AsyncMock)
    async def test_ban_rejects_offsite_redirect_and_bad_id(self, mock_upsert):
        resp = await self.client.post(
            '/users/ban',
            data={'user_id': 'abc', 'next': '//evil.example'},
            allow_redirects=False,
        )
        self.assertEqual(resp.headers['Location'], '/users')
        self.assertEqual(self.state.banned_users, {})
        mock_upsert.assert_not_awaited()

    @patch('dasovbot.database.delete_banned_user', new_callable=AsyncMock)
    async def test_unban(self, mock_delete):
        self.state.banned_users = {'7': {'banned_at': '', 'name': ''}}
        resp = await self.client.post('/users/unban', data={'user_id': '7'}, allow_redirects=False)
        self.assertEqual(resp.headers['Location'], '/users')
        self.assertFalse(self.state.is_banned('7'))


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


class TestHealthAlertsProcessor(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_when_app_has_no_state(self):
        # The context processor runs for error pages served before the state
        # is attached; it must degrade to an empty list, not crash
        from unittest.mock import MagicMock
        from dasovbot.dashboard.views import health_alerts_processor
        request = MagicMock()
        request.app = {}
        result = await health_alerts_processor(request)
        self.assertEqual(result, {'health_alerts': []})


if __name__ == '__main__':
    unittest.main()


def make_ha(role='passive'):
    from unittest.mock import MagicMock
    from dasovbot.services.ha import HaError
    ha = MagicMock()
    ha.config.ha_enabled = True
    ha.readiness_reason.return_value = 'last sync 95s behind the last heartbeat'
    ha.request_takeover = AsyncMock()
    ha.request_handback = AsyncMock()
    ha.HaError = HaError
    ha.status.return_value = {
        'enabled': True, 'role': role, 'node': 'rpi', 'node_role': 'standby', 'peer': 'dasovbot',
        'peer_url': 'http://192.168.11.150:8080', 'peer_role': 'active', 'lease_holder': 'dasovbot',
        'lease_until': None, 'rev': 10, 'drained': False, 'manual_hold': False, 'handback_requested': False,
        'handoff_rev': None, 'handoff_pending': False, 'peer_seen_at': None, 'ready': True,
        'last_sync_at': '20260101_000000', 'last_sync_rev': 39071, 'last_heartbeat_at': '20260101_000100',
        'last_snapshot_at': None,
    }
    return ha


class HaDashboardTestCase(DashboardViewTestCase):
    role = 'passive'

    async def get_application(self):
        self.state = make_state(
            config=make_config(),
            migration_progress={'status': 'skipped', 'tables': {}, 'elapsed': 0.0},
        )
        self.ha = make_ha(self.role)
        return create_app(self.state, self.ha)

    def set_role(self, role):
        self.ha.status.return_value['role'] = role


class TestPassiveDashboard(HaDashboardTestCase):
    async def test_post_actions_are_409_with_the_active_node(self):
        for path, data in (('/users/ban', {'user_id': '7'}), ('/intent/remove', {'url': 'x'}),
                           ('/system/populate', {}), ('/subscriptions/remove', {'url': 'x'})):
            resp = await self.client.post(path, data=data, allow_redirects=False)
            self.assertEqual(resp.status, 409, path)
            text = await resp.text()
            self.assertIn('PASSIVE', text)
            self.assertIn('http://192.168.11.150:8080', text)
        self.assertEqual(self.state.banned_users, {})

    async def test_get_pages_still_work_read_only(self):
        for path in ('/', '/videos', '/users', '/system'):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 200, path)

    async def test_banner_on_every_page(self):
        for path in ('/', '/videos', '/subscriptions', '/system'):
            text = await (await self.client.get(path)).text()
            self.assertIn('data-ha-banner="passive"', text, path)
            self.assertIn('active node is <a href="http://192.168.11.150:8080">dasovbot</a>', text)
            self.assertIn('role-chip role-passive', text)
            self.assertIn('>PASSIVE<', text)

    async def test_login_post_works_while_passive(self):
        with patch('dasovbot.dashboard.auth.get_password', return_value='pw'):
            resp = await self.client.post('/login', data={'password': 'pw'}, allow_redirects=False)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers['Location'], '/')

    async def test_system_card_and_take_over_button(self):
        text = await (await self.client.get('/system')).text()
        self.assertIn('data-ha-card', text)
        self.assertIn('rpi is the standby', text)
        self.assertIn('rev 39071', text)
        self.assertIn('action="/system/takeover"', text)
        self.assertNotIn('action="/system/handback"', text)
        self.assertNotIn('disabled', text.split('data-ha-card')[1].split('</form>')[0])

    async def test_take_over_disabled_when_not_ready(self):
        self.ha.status.return_value['ready'] = False
        text = await (await self.client.get('/system')).text()
        self.assertIn('✗', text)
        self.assertIn('last sync 95s behind', text)
        self.assertIn('disabled', text.split('action="/system/takeover"')[1].split('</form>')[0])

    async def test_takeover_calls_controller_and_redirects(self):
        resp = await self.client.post('/system/takeover', allow_redirects=False)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers['Location'], '/system')
        self.ha.request_takeover.assert_awaited_once()

    async def test_takeover_error_is_shown_on_system_page(self):
        from dasovbot.services.ha import HaError
        self.ha.request_takeover.side_effect = HaError('not ready: never synced from the peer')
        resp = await self.client.post('/system/takeover', allow_redirects=False)
        self.assertEqual(resp.status, 302)
        location = resp.headers['Location']
        self.assertTrue(location.startswith('/system?ha_error='))
        text = await (await self.client.get(location)).text()
        self.assertIn('data-ha-error', text)
        self.assertIn('never synced from the peer', text)

    async def test_ha_error_is_escaped(self):
        text = await (await self.client.get('/system?ha_error=%3Cscript%3Ex%3C/script%3E')).text()
        self.assertNotIn('<script>x</script>', text)
        self.assertIn('&lt;script&gt;', text)

    async def test_handback_on_passive_surfaces_controller_error(self):
        from dasovbot.services.ha import HaError
        self.ha.request_handback.side_effect = HaError('not active')
        resp = await self.client.post('/system/handback', allow_redirects=False)
        self.assertEqual(resp.headers['Location'], '/system?ha_error=not%20active')


class TestActiveDashboard(HaDashboardTestCase):
    role = 'active'

    @patch('dasovbot.database.upsert_banned_user', new_callable=AsyncMock)
    async def test_post_actions_unchanged(self, mock_upsert):
        resp = await self.client.post('/users/ban', data={'user_id': '7'}, allow_redirects=False)
        self.assertEqual(resp.status, 302)
        self.assertTrue(self.state.is_banned('7'))

    async def test_no_banner_but_chip_and_handback_button(self):
        text = await (await self.client.get('/system')).text()
        self.assertNotIn('data-ha-banner', text)
        self.assertIn('>ACTIVE<', text)
        self.assertIn('action="/system/handback"', text)
        self.assertIn('>Hand back<', text)
        self.assertNotIn('action="/system/takeover"', text)

    async def test_primary_label_reads_hand_over(self):
        self.ha.status.return_value['node_role'] = 'primary'
        text = await (await self.client.get('/system')).text()
        self.assertIn('Hand over to standby', text)

    async def test_handback_calls_controller(self):
        resp = await self.client.post('/system/handback', allow_redirects=False)
        self.assertEqual(resp.headers['Location'], '/system')
        self.ha.request_handback.assert_awaited_once()

    async def test_draining_shows_banner_and_no_buttons(self):
        self.set_role('draining')
        resp = await self.client.post('/users/ban', data={'user_id': '7'}, allow_redirects=False)
        self.assertEqual(resp.status, 409)
        self.assertIn('DRAINING', await resp.text())
        text = await (await self.client.get('/system')).text()
        self.assertIn('data-ha-banner="draining"', text)
        self.assertIn('draining…', text)
        self.assertNotIn('action="/system/takeover"', text)
        self.assertNotIn('action="/system/handback"', text)


class TestStandaloneDashboard(DashboardViewTestCase):
    async def test_no_ha_markup_without_controller(self):
        for path in ('/', '/system'):
            text = await (await self.client.get(path)).text()
            self.assertNotIn('data-ha', text, path)
            self.assertNotIn('class="role-chip', text, path)

    async def test_takeover_and_handback_are_404(self):
        self.assertEqual((await self.client.post('/system/takeover', allow_redirects=False)).status, 404)
        self.assertEqual((await self.client.post('/system/handback', allow_redirects=False)).status, 404)

    @patch('dasovbot.database.upsert_banned_user', new_callable=AsyncMock)
    async def test_posts_pass_without_controller(self, mock_upsert):
        resp = await self.client.post('/users/ban', data={'user_id': '7'}, allow_redirects=False)
        self.assertEqual(resp.status, 302)
