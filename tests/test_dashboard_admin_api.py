import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import AioHTTPTestCase

import dasovbot.dashboard.auth as auth_module
from dasovbot.dashboard.auth import check_admin_token, get_admin_token
from dasovbot.dashboard.server import create_app
from dasovbot.models import Intent, Subscription, VideoInfo
from tests.helpers import make_state, make_config

API_TOKEN = 'test-api-token'
ADMIN_TOKEN = 'test-admin-token'
READ = {'Authorization': f'Bearer {API_TOKEN}'}
ADMIN = {'Authorization': f'Bearer {ADMIN_TOKEN}'}


class AdminApiTestCase(AioHTTPTestCase):
    def setUp(self):
        self._env_patcher = patch.dict('os.environ', {'API_TOKEN': API_TOKEN, 'ADMIN_TOKEN': ADMIN_TOKEN})
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        super().setUp()

    async def get_application(self):
        self.state = make_state(
            config=make_config(),
            migration_progress={'status': 'skipped', 'tables': {}, 'elapsed': 0.0},
        )
        return create_app(self.state)

    async def _get_json(self, path, status=200, headers=ADMIN):
        resp = await self.client.get(path, headers=headers)
        self.assertEqual(resp.status, status, await resp.text())
        return await resp.json()


class TestAdminAuth(AdminApiTestCase):
    async def test_admin_token_opens_admin_routes(self):
        resp = await self.client.get('/api/status', headers=ADMIN)
        self.assertEqual(resp.status, 200)

    async def test_api_token_is_refused_on_admin_routes(self):
        resp = await self.client.get('/api/status', headers=READ)
        self.assertEqual(resp.status, 401)
        self.assertEqual(await resp.json(), {'error': 'unauthorized'})

    async def test_api_token_still_reads_videos(self):
        resp = await self.client.get('/api/videos', headers=READ)
        self.assertEqual(resp.status, 200)
        resp = await self.client.get('/api/videos/abc', headers=READ)
        self.assertEqual(resp.status, 404)

    async def test_admin_token_reads_videos_too(self):
        resp = await self.client.get('/api/videos', headers=ADMIN)
        self.assertEqual(resp.status, 200)

    async def test_api_token_is_refused_on_a_videos_lookalike_path(self):
        # /api/videosx is not under the read-only prefix
        resp = await self.client.get('/api/videosx', headers=READ)
        self.assertEqual(resp.status, 401)

    async def test_missing_token_is_refused_and_logged(self):
        with self.assertLogs('dasovbot.dashboard.auth', 'WARNING') as logs:
            resp = await self.client.get('/api/users')
        self.assertEqual(resp.status, 401)
        self.assertEqual(len(logs.records), 1)
        self.assertIn('GET /api/users', logs.output[0])

    async def test_accepted_request_is_not_logged(self):
        with self.assertNoLogs('dasovbot.dashboard.auth', 'WARNING'):
            resp = await self.client.get('/api/users', headers=ADMIN)
        self.assertEqual(resp.status, 200)


class TestGeneratedAdminToken(unittest.TestCase):
    def setUp(self):
        auth_module._generated_admin_token = None

    def test_generates_stable_token_without_env(self):
        with patch.dict('os.environ', {}, clear=True):
            token = get_admin_token()
            self.assertGreaterEqual(len(token), 32)
            self.assertEqual(get_admin_token(), token)

    def test_check_accepts_generated_token(self):
        with patch.dict('os.environ', {}, clear=True):
            token = get_admin_token()
            request = MagicMock()
            request.headers = {'Authorization': f'Bearer {token}'}
            self.assertTrue(check_admin_token(request))

    def test_env_token_wins(self):
        with patch.dict('os.environ', {'ADMIN_TOKEN': 'from-env'}):
            self.assertEqual(get_admin_token(), 'from-env')


class TestApiStatus(AdminApiTestCase):
    async def test_health_fields_counts_and_intent_split(self):
        self.state.users = {'1': {'first_name': 'A'}, '2': {'first_name': 'B'}}
        self.state.banned_users = {'2': {'banned_at': '20260101_000000', 'name': 'B'}}
        video = VideoInfo(title='V', file_id='f', video_id='vid1', webpage_url='https://x/v')
        self.state.videos = {'https://x/v': video, 'q': video}
        self.state.subscriptions = {
            'https://x/c/videos': Subscription(chat_ids=['1', '2'], title='C'),
            'https://x/d/videos': Subscription(chat_ids=['1'], title='D'),
        }
        self.state.intents = {
            'https://x/pending': Intent(priority=2),
            'https://x/retrying': Intent(priority=0),
            'https://x/retrying2': Intent(priority=0, retries=1),
            'https://x/ignored': Intent(ignored=True),
        }
        self.state.intent_retry_after['https://x/retrying'] = time.monotonic() + 600
        self.state.background_task_status = {'populate_subscriptions': '20260101_120000'}
        self.state.set_alert('backup_stale', 'old')

        body = await self._get_json('/api/status')

        self.assertEqual(body['role'], 'active')
        self.assertFalse(body['enabled'])
        self.assertEqual(body['counts']['users'], 2)
        self.assertEqual(body['counts']['banned'], 1)
        self.assertEqual(body['counts']['videos'], 1)
        self.assertEqual(body['counts']['videos_rows'], 2)
        self.assertEqual(body['counts']['subscriptions'], 2)
        self.assertEqual(body['counts']['subscribers'], 3)
        self.assertEqual(body['counts']['intents'], {'pending': 1, 'retrying': 2, 'ignored': 1, 'total': 4})
        self.assertEqual(body['counts']['alerts'], 1)
        self.assertEqual(body['tasks']['populate_subscriptions'], '20260101_120000')
        self.assertIsNone(body['tasks']['monitor_backups'])
        self.assertIn('monitor_disk_space', body['tasks'])
        self.assertIsNone(body['vpn'])
        self.assertEqual(body['queue_size'], 0)
        self.assertEqual(body['migration']['status'], 'skipped')
        self.assertTrue(body['node_time'])

    async def test_backup_block_without_backups(self):
        body = await self._get_json('/api/status')
        self.assertEqual(body['backup']['dir'], '/tmp/test/data')
        self.assertIsNone(body['backup']['newest_age_sec'])

    async def test_backup_block_is_null_without_config(self):
        self.state.config = None
        body = await self._get_json('/api/status')
        self.assertIsNone(body['backup'])


class TestApiUsers(AdminApiTestCase):
    def _seed(self):
        self.state.users = {
            '10': {'first_name': 'Alice', 'last_name': 'Smith', 'username': 'alice'},
            '20': {'first_name': 'Bob'},
        }
        self.state.user_requests = {
            '10': {'count': 5, 'last_at': '20260102_000000'},
            '20': {'count': 7, 'last_at': '20260101_000000'},
        }
        self.state.banned_users = {'30': {'banned_at': '20260103_000000', 'name': 'Carol @carol'}}

    async def test_lists_every_known_user_sorted_by_requests(self):
        self._seed()
        items = await self._get_json('/api/users')
        self.assertEqual([item['id'] for item in items], ['20', '10', '30'])
        alice = items[1]
        self.assertEqual(alice['name'], 'Alice Smith')
        self.assertEqual(alice['username'], 'alice')
        self.assertEqual(alice['requests'], 5)
        self.assertFalse(alice['banned'])
        carol = items[2]
        self.assertEqual(carol['name'], 'Carol @carol')
        self.assertIsNone(carol['username'])
        self.assertTrue(carol['banned'])
        self.assertEqual(carol['banned_at'], '20260103_000000')
        self.assertEqual(carol['requests'], 0)

    async def test_search_by_username_with_at(self):
        self._seed()
        items = await self._get_json('/api/users?search=@ALICE')
        self.assertEqual([item['id'] for item in items], ['10'])

    async def test_search_by_id_and_name(self):
        self._seed()
        self.assertEqual([item['id'] for item in await self._get_json('/api/users?search=20')], ['20'])
        self.assertEqual([item['id'] for item in await self._get_json('/api/users?search=smith')], ['10'])

    async def test_banned_filter(self):
        self._seed()
        self.assertEqual([item['id'] for item in await self._get_json('/api/users?banned=true')], ['30'])
        self.assertEqual([item['id'] for item in await self._get_json('/api/users?banned=false')], ['20', '10'])


class TestApiSubscriptions(AdminApiTestCase):
    async def test_entries_and_chat_id_filter(self):
        self.state.users = {'1': {'first_name': 'Ann', 'username': 'ann'}}
        self.state.subscriptions = {
            'https://x/c/videos': Subscription(chat_ids=['1', '2'], title='Zed', uploader='Z', uploader_videos='https://x/c/videos'),
            'https://x/d/videos': Subscription(chat_ids=['2'], title='alpha', uploader='A', uploader_videos='https://x/d/videos'),
        }
        items = await self._get_json('/api/subscriptions')
        self.assertEqual([item['title'] for item in items], ['alpha', 'Zed'])
        zed = items[1]
        self.assertEqual(zed['url'], 'https://x/c/videos')
        self.assertEqual(zed['uploader'], 'Z')
        self.assertEqual(zed['chat_ids'], [{'id': '1', 'name': 'Ann @ann'}, {'id': '2', 'name': ''}])
        items = await self._get_json('/api/subscriptions?chat_id=1')
        self.assertEqual([item['title'] for item in items], ['Zed'])


class TestApiIntents(AdminApiTestCase):
    async def test_list_sorted_by_priority_with_ignored_filter(self):
        self.state.intents = {
            'https://x/low': Intent(priority=1, title='Low', source='download', chat_ids=['1']),
            'https://x/high': Intent(priority=5, title='High', inline_message_ids=['i1']),
            'https://x/ignored': Intent(priority=9, ignored=True),
        }
        items = await self._get_json('/api/intents')
        self.assertEqual([item['url'] for item in items], ['https://x/ignored', 'https://x/high', 'https://x/low'])
        high = items[1]
        self.assertEqual(high['status'], 'pending')
        self.assertEqual(high['inline_count'], 1)
        self.assertEqual(high['retry_in_sec'], 0)
        self.assertEqual(items[2]['chat_ids'], ['1'])
        items = await self._get_json('/api/intents?ignored=false')
        self.assertEqual([item['url'] for item in items], ['https://x/high', 'https://x/low'])
        items = await self._get_json('/api/intents?ignored=true')
        self.assertEqual([item['status'] for item in items], ['ignored'])

    async def test_retrying_reports_the_remaining_backoff(self):
        self.state.intents = {'https://x/r': Intent()}
        self.state.intent_retry_after['https://x/r'] = time.monotonic() + 120
        items = await self._get_json('/api/intents')
        self.assertEqual(items[0]['status'], 'retrying')
        self.assertGreater(items[0]['retry_in_sec'], 100)

    async def test_url_lookup_pending_delivered_absent(self):
        self.state.intents = {'https://x/p': Intent(priority=2)}
        self.state.videos = {'https://x/done': VideoInfo(title='D', file_id='f1'),
                             'https://x/nofile': VideoInfo(title='N')}
        body = await self._get_json('/api/intents?url=https://x/p')
        self.assertEqual(body['status'], 'pending')
        self.assertEqual(body['intent']['priority'], 2)
        body = await self._get_json('/api/intents?url=https://x/done')
        self.assertEqual(body, {'url': 'https://x/done', 'status': 'delivered', 'intent': None})
        body = await self._get_json('/api/intents?url=https://x/nofile')
        self.assertEqual(body['status'], 'absent')
        body = await self._get_json('/api/intents?url=https://x/never')
        self.assertEqual(body['status'], 'absent')


class TestApiAlerts(AdminApiTestCase):
    async def test_lists_alerts(self):
        self.state.set_alert('disk_media', 'Low disk', level='warning')
        items = await self._get_json('/api/alerts')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['id'], 'disk_media')
        self.assertEqual(items[0]['message'], 'Low disk')
        self.assertTrue(items[0]['since'])


class TestApiPlaylists(AdminApiTestCase):
    async def test_rejects_missing_or_non_http_url(self):
        body = await self._get_json('/api/playlists', status=400)
        self.assertEqual(body['error'], 'invalid url')
        body = await self._get_json('/api/playlists?url=ftp://x', status=400)
        self.assertEqual(body['error'], 'invalid url')

    @patch('dasovbot.handlers.subscription._extract_info', new_callable=AsyncMock)
    async def test_lists_playlists(self, mock_extract):
        uploader_url = 'https://example.com/c'

        async def extract(url):
            if url == 'https://example.com/v1':
                return {'uploader_url': uploader_url}
            if url == uploader_url:
                return {}
            if url == f'{uploader_url}/playlists':
                return {'entries': [{'title': 'P1', 'url': f'{uploader_url}/p1'}], 'uploader': 'Uploader'}
            if url == f'{uploader_url}/streams':
                return {}
            raise ValueError(url)

        mock_extract.side_effect = extract
        body = await self._get_json('/api/playlists?url=https://example.com/v1')
        self.assertEqual(body['uploader'], 'Uploader')
        self.assertEqual(body['uploader_url'], uploader_url)
        self.assertEqual(body['uploader_videos'], f'{uploader_url}/videos')
        self.assertFalse(body['direct'])
        self.assertFalse(body['fallback_videos'])
        self.assertEqual(body['playlists'], [
            {'title': 'Uploader Videos', 'url': f'{uploader_url}/videos'},
            {'title': 'Uploader Streams', 'url': f'{uploader_url}/streams'},
            {'title': 'P1', 'url': f'{uploader_url}/p1'},
        ])

    @patch('dasovbot.handlers.subscription._extract_info', new_callable=AsyncMock)
    async def test_falls_back_to_videos_without_a_playlists_tab(self, mock_extract):
        uploader_url = 'https://example.com/c'

        async def extract(url):
            if url == uploader_url:
                return {'uploader_url': uploader_url, 'uploader': 'Up'}
            raise ValueError(url)

        mock_extract.side_effect = extract
        body = await self._get_json(f'/api/playlists?url={uploader_url}')
        self.assertTrue(body['fallback_videos'])
        self.assertEqual(body['playlists'], [{'title': 'Up Videos', 'url': f'{uploader_url}/videos'}])

    @patch('dasovbot.handlers.subscription._extract_info', new_callable=AsyncMock)
    async def test_unsupported_url(self, mock_extract):
        mock_extract.return_value = {'title': 'just a video'}
        body = await self._get_json('/api/playlists?url=https://example.com/v', status=400)
        self.assertEqual(body['error'], 'unsupported url')

    @patch('dasovbot.handlers.subscription._extract_info', new_callable=AsyncMock)
    async def test_extraction_error_is_502(self, mock_extract):
        mock_extract.side_effect = RuntimeError('boom')
        body = await self._get_json('/api/playlists?url=https://example.com/v', status=502)
        self.assertEqual(body['error'], 'extraction failed')
        self.assertEqual(body['detail'], 'boom')


def make_ha(role='active'):
    ha = MagicMock()
    ha.status.return_value = {
        'enabled': True, 'role': role, 'node': 'rpi', 'node_role': 'standby', 'peer': 'dasovbot',
        'peer_url': 'http://192.168.1.10:8080', 'lease_holder': 'dasovbot', 'rev': 10, 'ready': True,
        'drained': False, 'manual_hold': False, 'handback_requested': False,
        'last_sync_at': None, 'last_sync_rev': None,
    }
    ha.notifier = MagicMock()
    ha.notifier.notify = AsyncMock()
    return ha


class AdminWriteTestCase(AdminApiTestCase):
    role = 'active'

    async def get_application(self):
        self.state = make_state(
            config=make_config(),
            migration_progress={'status': 'skipped', 'tables': {}, 'elapsed': 0.0},
        )
        self.ha = make_ha(self.role)
        return create_app(self.state, self.ha)

    async def _post_json(self, path, body=None, status=200):
        resp = await self.client.post(path, json=body if body is not None else {}, headers=ADMIN)
        self.assertEqual(resp.status, status, await resp.text())
        return await resp.json()

    def audit_lines(self):
        return [call.args[0] for call in self.ha.notifier.notify.await_args_list]

    def seed_users(self):
        self.state.users = {
            '10': {'first_name': 'Alice', 'last_name': 'Smith', 'username': 'alice'},
            '20': {'first_name': 'Bob', 'username': 'bob'},
            '21': {'first_name': 'Bobby', 'username': 'BOB'},
            '30': {'first_name': 'Carol'},
        }
        self.state.user_requests = {'10': {'count': 3, 'last_at': '20260102_000000'}}


class TestBodyParsing(AdminWriteTestCase):
    async def test_invalid_json_is_400(self):
        resp = await self.client.post('/api/users/10/ban', data='{not json', headers=ADMIN)
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertEqual(body['error'], 'invalid json')

    async def test_non_object_body_is_400(self):
        resp = await self.client.post('/api/users/10/ban', json=[1, 2], headers=ADMIN)
        self.assertEqual(resp.status, 400)
        self.assertEqual((await resp.json())['error'], 'invalid body')

    async def test_empty_body_is_fine(self):
        resp = await self.client.post('/api/users/10/ban', headers=ADMIN)
        self.assertEqual(resp.status, 200)

    async def test_api_token_cannot_write(self):
        resp = await self.client.post('/api/users/10/ban', json={}, headers=READ)
        self.assertEqual(resp.status, 401)
        self.assertEqual(self.state.banned_users, {})


class TestBanUnban(AdminWriteTestCase):
    async def test_ban_by_id_writes_and_audits(self):
        self.seed_users()
        body = await self._post_json('/api/users/10/ban')
        self.assertEqual(body['action'], 'ban')
        self.assertTrue(body['changed'])
        self.assertTrue(body['user']['banned'])
        self.assertEqual(body['user']['name'], 'Alice Smith')
        self.assertIn('10', self.state.banned_users)
        self.assertEqual(self.state.banned_users['10']['name'], 'Alice Smith @alice')
        self.assertEqual(self.audit_lines(), ['API: banned Alice Smith @alice (10)'])

    async def test_ban_by_username(self):
        self.seed_users()
        body = await self._post_json('/api/users/@alice/ban')
        self.assertEqual(body['user']['id'], '10')
        self.assertIn('10', self.state.banned_users)

    async def test_ban_never_seen_id_is_allowed(self):
        body = await self._post_json('/api/users/999/ban')
        self.assertTrue(body['changed'])
        self.assertEqual(body['user']['name'], '')
        self.assertIn('999', self.state.banned_users)
        self.assertEqual(self.audit_lines(), ['API: banned 999'])

    async def test_ambiguous_username_is_409_with_candidates(self):
        self.seed_users()
        body = await self._post_json('/api/users/@bob/ban', status=409)
        self.assertEqual(body['error'], 'ambiguous user')
        self.assertEqual(sorted(item['id'] for item in body['candidates']), ['20', '21'])
        self.assertEqual(self.state.banned_users, {})

    async def test_unknown_username_is_404_with_candidates(self):
        self.seed_users()
        body = await self._post_json('/api/users/@ali/ban', status=404)
        self.assertEqual(body['error'], 'unknown user')
        self.assertEqual([item['id'] for item in body['candidates']], ['10'])
        self.assertEqual(self.state.banned_users, {})

    async def test_invalid_user_is_400(self):
        body = await self._post_json('/api/users/alice/ban', status=400)
        self.assertEqual(body['error'], 'invalid user')

    async def test_dry_run_writes_nothing(self):
        self.seed_users()
        body = await self._post_json('/api/users/10/ban', {'dry_run': True})
        self.assertEqual(body, {'dry_run': True, 'action': 'ban', 'user': body['user']})
        self.assertFalse(body['user']['banned'])
        self.assertEqual(self.state.banned_users, {})
        self.state.db.execute.assert_not_called()
        self.ha.notifier.notify.assert_not_awaited()

    async def test_ban_twice_reports_unchanged(self):
        await self._post_json('/api/users/10/ban')
        body = await self._post_json('/api/users/10/ban')
        self.assertFalse(body['changed'])
        self.assertEqual(len(self.audit_lines()), 1)

    async def test_unban(self):
        self.seed_users()
        self.state.banned_users = {'10': {'banned_at': '20260101_000000', 'name': 'Alice Smith @alice'}}
        body = await self._post_json('/api/users/10/unban')
        self.assertEqual(body['action'], 'unban')
        self.assertTrue(body['changed'])
        self.assertFalse(body['user']['banned'])
        self.assertEqual(self.state.banned_users, {})
        self.assertEqual(self.audit_lines(), ['API: unbanned Alice Smith @alice (10)'])
        body = await self._post_json('/api/users/10/unban')
        self.assertFalse(body['changed'])


class TestWritesWithoutController(AdminApiTestCase):
    async def test_audit_is_skipped_without_a_controller(self):
        # The standalone app (tests, preview_dashboard.py) has no ha and must not fail
        resp = await self.client.post('/api/users/5/ban', json={}, headers=ADMIN)
        self.assertEqual(resp.status, 200)
        self.assertTrue((await resp.json())['changed'])
        self.assertIn('5', self.state.banned_users)


class TestSubscribe(AdminWriteTestCase):
    def _found(self, uploader_url='https://example.com/c', **overrides):
        playlists = {
            'a': {'title': 'Up Videos', 'url': f'{uploader_url}/videos', 'pinned': True},
            'b': {'title': 'Up Streams', 'url': f'{uploader_url}/streams', 'pinned': True},
            'c': {'title': 'P1', 'url': f'{uploader_url}/p1', 'pinned': False},
        }
        found = {'uploader': 'Up', 'uploader_url': uploader_url, 'uploader_videos': f'{uploader_url}/videos',
                 'playlists': playlists, 'direct': False, 'fallback_videos': False}
        found.update(overrides)
        return found

    async def test_rejects_missing_url(self):
        body = await self._post_json('/api/subscriptions', {}, status=400)
        self.assertEqual(body['error'], 'invalid url')

    async def test_existing_subscription_adds_the_developer_chat(self):
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['7'], title='X', uploader='X')}
        body = await self._post_json('/api/subscriptions', {'url': 'https://x/c/videos'})
        self.assertEqual(body['action'], 'add_subscriber')
        self.assertEqual(body['url'], 'https://x/c/videos')
        self.assertEqual(body['subscription']['chat_ids'], ['7', '123'])
        self.assertEqual(body['populate'], 'skipped')
        self.assertEqual(self.state.subscriptions['https://x/c/videos'].chat_ids, ['7', '123'])
        self.ha.notifier.notify.assert_not_awaited()

    async def test_already_subscribed(self):
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['123'], title='X')}
        body = await self._post_json('/api/subscriptions', {'url': 'https://x/c/videos'})
        self.assertEqual(body['status'], 'already_subscribed')

    async def test_dry_run_on_existing_writes_nothing(self):
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['7'], title='X')}
        body = await self._post_json('/api/subscriptions', {'url': 'https://x/c/videos', 'dry_run': True})
        self.assertEqual(body['action'], 'add_subscriber')
        self.assertTrue(body['dry_run'])
        self.assertEqual(body['subscription']['chat_ids'], ['7', '123'])
        self.assertEqual(self.state.subscriptions['https://x/c/videos'].chat_ids, ['7'])

    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_bare_channel_subscribes_to_videos(self, mock_discover):
        mock_discover.return_value = self._found()
        body = await self._post_json('/api/subscriptions', {'url': 'https://example.com/c/'})
        self.assertEqual(body['action'], 'subscribe')
        self.assertEqual(body['url'], 'https://example.com/c/videos')
        sub = self.state.subscriptions['https://example.com/c/videos']
        self.assertEqual(sub.title, 'Up Videos')
        self.assertEqual(sub.uploader, 'Up')
        self.assertEqual(sub.uploader_videos, 'https://example.com/c/videos')
        self.assertEqual(sub.chat_ids, ['123'])
        self.assertNotIn('https://example.com/c/', self.state.subscriptions)

    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_playlist_of_the_channel_subscribes_to_that_entry(self, mock_discover):
        mock_discover.return_value = self._found(direct=True)
        body = await self._post_json('/api/subscriptions', {'url': 'https://example.com/c/p1', 'chat_id': 123})
        self.assertEqual(body['url'], 'https://example.com/c/p1')
        sub = self.state.subscriptions['https://example.com/c/p1']
        self.assertEqual(sub.title, 'P1')
        self.assertEqual(sub.uploader_videos, 'https://example.com/c/videos')

    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_video_url_is_refused_with_the_choices(self, mock_discover):
        mock_discover.return_value = self._found()
        body = await self._post_json('/api/subscriptions', {'url': 'https://example.com/v?id=1'}, status=409)
        self.assertEqual(body['error'], 'ambiguous url')
        self.assertEqual([item['url'] for item in body['playlists']][:1], ['https://example.com/c/videos'])
        self.assertEqual(self.state.subscriptions, {})

    @patch('dasovbot.dashboard.admin_api.resolve_subscription', new_callable=AsyncMock)
    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_channel_without_playlists_tab_resolves_its_videos_feed(self, mock_discover, mock_resolve):
        mock_discover.return_value = self._found(fallback_videos=True)
        mock_resolve.return_value = Subscription(title='Up - Videos', uploader='Up', uploader_videos='https://example.com/c/videos')
        body = await self._post_json('/api/subscriptions', {'url': 'https://example.com/c'})
        mock_resolve.assert_awaited_once_with('https://example.com/c/videos')
        self.assertEqual(body['url'], 'https://example.com/c/videos')
        self.assertEqual(body['subscription']['title'], 'Up - Videos')

    @patch('dasovbot.dashboard.admin_api.resolve_subscription', new_callable=AsyncMock)
    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_non_channel_url_subscribes_as_is(self, mock_discover, mock_resolve):
        mock_discover.side_effect = ValueError('no playlists')
        mock_resolve.return_value = Subscription(title='Plain', uploader='U', uploader_videos='https://other/u/videos')
        body = await self._post_json('/api/subscriptions', {'url': 'https://other/list'})
        self.assertEqual(body['url'], 'https://other/list')
        self.assertEqual(self.state.subscriptions['https://other/list'].title, 'Plain')

    @patch('dasovbot.dashboard.admin_api.resolve_subscription', new_callable=AsyncMock)
    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_extraction_failure_is_502(self, mock_discover, mock_resolve):
        mock_discover.side_effect = ValueError('boom')
        mock_resolve.side_effect = RuntimeError('dead')
        body = await self._post_json('/api/subscriptions', {'url': 'https://other/list'}, status=502)
        self.assertEqual(body, {'error': 'extraction failed', 'detail': 'dead'})

    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_unsupported_url_is_400(self, mock_discover):
        mock_discover.return_value = self._found(uploader_url=None, uploader_videos=None, playlists={})
        body = await self._post_json('/api/subscriptions', {'url': 'https://other/v'}, status=400)
        self.assertEqual(body['error'], 'unsupported url')

    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_dry_run_on_new_subscription(self, mock_discover):
        mock_discover.return_value = self._found()
        body = await self._post_json('/api/subscriptions', {'url': 'https://example.com/c', 'dry_run': 'true'})
        self.assertEqual(body['action'], 'subscribe')
        self.assertEqual(body['url'], 'https://example.com/c/videos')
        self.assertEqual(body['subscription']['chat_ids'], ['123'])
        self.assertEqual(self.state.subscriptions, {})

    @patch('dasovbot.dashboard.admin_api.populate_playlist', new_callable=AsyncMock)
    @patch('dasovbot.dashboard.admin_api.discover_playlists', new_callable=AsyncMock)
    async def test_populate_schedules_the_feed_for_that_chat(self, mock_discover, mock_populate):
        mock_discover.return_value = self._found()
        body = await self._post_json('/api/subscriptions', {'url': 'https://example.com/c', 'populate': True})
        self.assertEqual(body['populate'], 'started')
        await asyncio.gather(*self.state.background_tasks)
        mock_populate.assert_awaited_once_with('https://example.com/c/videos', ['123'], self.state)

    async def test_unknown_chat_is_404_until_forced(self):
        self.state.users = {'1234': {'first_name': 'Near'}, '9': {}}
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['7'], title='X')}
        body = await self._post_json('/api/subscriptions', {'url': 'https://x/c/videos', 'chat_id': '1299'}, status=404)
        self.assertEqual(body['error'], 'unknown chat')
        # Both share the '12' prefix; the developer chat counts as known
        self.assertEqual([item['id'] for item in body['candidates']], ['1234', '123'])
        body = await self._post_json('/api/subscriptions', {'url': 'https://x/c/videos', 'chat_id': '1299', 'force': True})
        self.assertEqual(body['subscription']['chat_ids'], ['7', '1299'])

    async def test_chat_by_username(self):
        self.seed_users()
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['7'], title='X')}
        body = await self._post_json('/api/subscriptions', {'url': 'https://x/c/videos', 'chat_id': '@alice'})
        self.assertEqual(body['subscription']['chat_ids'], ['7', '10'])

    async def test_group_chat_ids_are_accepted(self):
        self.state.users = {'-1001': {'title': 'Group'}}
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['7'], title='X')}
        body = await self._post_json('/api/subscriptions', {'url': 'https://x/c/videos', 'chat_id': '-1001'})
        self.assertEqual(body['subscription']['chat_ids'], ['7', '-1001'])


class TestUnsubscribe(AdminWriteTestCase):
    async def test_unknown_url_is_404(self):
        body = await self._post_json('/api/subscriptions/remove', {'url': 'https://x/none'}, status=404)
        self.assertEqual(body['error'], 'not subscribed')

    async def test_chat_not_subscribed_is_404(self):
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['7'], title='X')}
        body = await self._post_json('/api/subscriptions/remove', {'url': 'https://x/c/videos'}, status=404)
        self.assertEqual(body['error'], 'chat not subscribed')

    async def test_removes_one_of_two_chats(self):
        self.state.users = {'7': {'first_name': 'Seven'}}
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['7', '123'], title='X')}
        body = await self._post_json('/api/subscriptions/remove', {'url': 'https://x/c/videos', 'chat_id': '7'})
        self.assertEqual(body, {'action': 'remove_subscriber', 'url': 'https://x/c/videos', 'chat_id': '7',
                                'removed_subscription': False})
        self.assertEqual(self.state.subscriptions['https://x/c/videos'].chat_ids, ['123'])
        self.assertEqual(self.audit_lines(), ['API: unsubscribed Seven (7) from X'])

    async def test_last_subscriber_removes_the_subscription(self):
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['123'], title='X')}
        body = await self._post_json('/api/subscriptions/remove', {'url': 'https://x/c/videos'})
        self.assertTrue(body['removed_subscription'])
        self.assertEqual(self.state.subscriptions, {})
        self.assertEqual(self.audit_lines(), ['API: unsubscribed 123 from X (subscription removed)'])

    async def test_dry_run(self):
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['123'], title='X')}
        body = await self._post_json('/api/subscriptions/remove', {'url': 'https://x/c/videos', 'dry_run': True})
        self.assertEqual(body, {'dry_run': True, 'action': 'remove_subscriber', 'url': 'https://x/c/videos',
                                'chat_id': '123', 'last_subscriber': True})
        self.assertIn('https://x/c/videos', self.state.subscriptions)
        self.ha.notifier.notify.assert_not_awaited()


class TestSend(AdminWriteTestCase):
    async def test_queues_an_intent_for_the_developer_chat(self):
        body = await self._post_json('/api/intents', {'url': 'https://x/v1'})
        self.assertEqual(body['action'], 'queue')
        self.assertEqual(body['chat_id'], '123')
        self.assertFalse(body['cached'])
        self.assertEqual(body['intent']['source'], 'api')
        self.assertEqual(body['intent']['chat_ids'], ['123'])
        self.assertEqual(body['intent']['status'], 'pending')
        intent = self.state.intents['https://x/v1']
        self.assertEqual(intent.source, 'api')
        self.assertEqual(intent.priority, 1)
        self.assertEqual(self.state.download_queue.qsize(), 1)
        self.ha.notifier.notify.assert_not_awaited()

    async def test_cached_flag_and_existing_intent(self):
        self.state.videos = {'https://x/v1': VideoInfo(title='V', file_id='f1')}
        self.state.intents = {'https://x/v1': Intent(chat_ids=['7'], priority=1, source='download')}
        body = await self._post_json('/api/intents', {'url': 'https://x/v1', 'dry_run': True})
        self.assertEqual(body, {'dry_run': True, 'action': 'queue', 'url': 'https://x/v1', 'chat_id': '123',
                                'cached': True, 'existing_intent': True})
        self.assertEqual(self.state.intents['https://x/v1'].chat_ids, ['7'])
        body = await self._post_json('/api/intents', {'url': 'https://x/v1'})
        self.assertTrue(body['cached'])
        self.assertEqual(body['intent']['chat_ids'], ['7', '123'])
        # The first requester's source is kept
        self.assertEqual(body['intent']['source'], 'download')

    async def test_banned_chat_is_403(self):
        self.state.banned_users = {'123': {'banned_at': 'x', 'name': ''}}
        body = await self._post_json('/api/intents', {'url': 'https://x/v1'}, status=403)
        self.assertEqual(body['error'], 'chat banned')
        self.assertEqual(self.state.intents, {})

    async def test_rejects_non_http_url(self):
        body = await self._post_json('/api/intents', {'url': 'cat video'}, status=400)
        self.assertEqual(body['error'], 'invalid url')


class TestPopulate(AdminWriteTestCase):
    @patch('dasovbot.dashboard.admin_api.run_populate_subscriptions', new_callable=AsyncMock)
    async def test_starts_a_full_run(self, mock_run):
        body = await self._post_json('/api/populate')
        self.assertEqual(body, {'status': 'started', 'scope': 'all'})
        await asyncio.gather(*self.state.background_tasks)
        mock_run.assert_awaited_once_with(self.state)

    async def test_reports_already_running(self):
        import dasovbot.services.background as background
        async with background._populate_lock:
            body = await self._post_json('/api/populate')
        self.assertEqual(body, {'status': 'already_running', 'scope': 'all'})

    @patch('dasovbot.dashboard.admin_api.populate_playlist', new_callable=AsyncMock)
    async def test_single_subscription(self, mock_populate):
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['7', '8'], title='X')}
        body = await self._post_json('/api/populate', {'url': 'https://x/c/videos'})
        self.assertEqual(body, {'status': 'started', 'scope': 'https://x/c/videos'})
        await asyncio.gather(*self.state.background_tasks)
        mock_populate.assert_awaited_once_with('https://x/c/videos', ['7', '8'], self.state)

    async def test_unknown_subscription_is_404(self):
        body = await self._post_json('/api/populate', {'url': 'https://x/none'}, status=404)
        self.assertEqual(body['error'], 'not subscribed')


class TestPassiveNode(AdminWriteTestCase):
    role = 'passive'

    async def test_every_write_is_a_json_409(self):
        self.state.subscriptions = {'https://x/c/videos': Subscription(chat_ids=['123'], title='X')}
        for path, data in (('/api/users/1/ban', {}), ('/api/users/1/unban', {}),
                           ('/api/subscriptions', {'url': 'https://x/c/videos'}),
                           ('/api/subscriptions/remove', {'url': 'https://x/c/videos'}),
                           ('/api/intents', {'url': 'https://x/v'}), ('/api/populate', {})):
            body = await self._post_json(path, data, status=409)
            self.assertEqual(body['error'], 'passive', path)
            self.assertEqual(body['active'], 'http://192.168.1.10:8080', path)
            self.assertIn('PASSIVE', body['detail'], path)
        self.assertEqual(self.state.banned_users, {})
        self.assertEqual(self.state.intents, {})
        self.assertEqual(self.state.subscriptions['https://x/c/videos'].chat_ids, ['123'])

    async def test_reads_still_work(self):
        body = await self._get_json('/api/status')
        self.assertEqual(body['role'], 'passive')
