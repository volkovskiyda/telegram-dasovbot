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
