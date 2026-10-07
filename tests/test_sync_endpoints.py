"""/health and /sync/* endpoints (plan item 07)."""
import json
import os
import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import AioHTTPTestCase

from dasovbot.dashboard.server import create_app
from dasovbot.models import VideoInfo
from dasovbot.services.ha import HaError
from dasovbot.state import BotState
from tests.helpers import make_config, make_memory_db

SECRET = 'shared-secret'
AUTH = {'Authorization': f'Bearer {SECRET}'}


def make_controller(role='active', **status):
    ha = MagicMock()
    ha.role = role
    ha.config.lease_ttl_sec = 30.0
    ha.status.return_value = {
        'enabled': True, 'role': role, 'node': 'me', 'node_role': 'standby', 'peer': 'other',
        'peer_url': 'http://192.168.11.150:8080', 'lease_holder': 'me' if role == 'active' else 'other',
        'lease_until': None, 'rev': 42, 'drained': False, 'manual_hold': False, 'handback_requested': False,
        'handoff_rev': 40, 'handoff_pending': False, 'peer_role': 'passive', 'peer_seen_at': '20261007_120000',
        'ready': True, 'last_sync_at': '20261007_120000', 'last_sync_rev': 41,
        'last_heartbeat_at': '20261007_120005', 'last_snapshot_at': None, **status,
    }
    ha.handoff_requested_by_peer = AsyncMock(return_value={'accepted': True, 'role': 'draining', 'drained': False})
    return ha


class SyncEndpointTestCase(AioHTTPTestCase):
    controller = None

    def setUp(self):
        self._env = patch.dict('os.environ', {'SYNC_SECRET': SECRET})
        self._env.start()
        self.addCleanup(self._env.stop)
        super().setUp()

    async def get_application(self):
        self.state = BotState(db=await make_memory_db(), config=make_config(node_name='me'))
        self.addAsyncCleanup(self.state.db.close)
        return create_app(self.state, self.controller)


class TestHealthStandalone(SyncEndpointTestCase):
    async def test_health_without_controller_reports_active(self):
        resp = await self.client.get('/health')
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual((body['role'], body['node'], body['lease_holder'], body['ready'], body['enabled']),
                         ('active', 'me', 'me', True, False))

    async def test_heartbeat_and_handoff_are_503_without_controller(self):
        self.assertEqual((await self.client.get('/sync/heartbeat', headers=AUTH)).status, 503)
        self.assertEqual((await self.client.post('/sync/handoff', headers=AUTH, json={})).status, 503)

    async def test_changes_served_without_controller(self):
        await self.state.set_video('v', VideoInfo(title='T'))
        resp = await self.client.get('/sync/changes', headers=AUTH)
        self.assertEqual(resp.status, 200)
        page = await resp.json()
        self.assertEqual(page['rows']['videos'][0][0], 'v')


class TestHealthWithController(SyncEndpointTestCase):
    controller = make_controller(role='passive')

    async def test_health_shape(self):
        resp = await self.client.get('/health')
        body = await resp.json()
        self.assertEqual(body['role'], 'passive')
        self.assertEqual(body['lease_holder'], 'other')
        self.assertEqual(body['peer_url'], 'http://192.168.11.150:8080')
        self.assertTrue(body['ready'])
        self.assertNotIn('peer_seen_at', body)  # filtered to the documented keys
        self.assertNotIn('handoff_pending', body)

    async def test_heartbeat_fields(self):
        resp = await self.client.get('/sync/heartbeat', headers=AUTH)
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(set(body), {'node', 'role', 'lease_until', 'rev', 'drained', 'manual_hold',
                                     'handback_requested', 'handoff_rev'})
        self.assertEqual((body['node'], body['rev'], body['handoff_rev']), ('me', 42, 40))
        self.assertRegex(body['lease_until'], r'^\d{8}_\d{6}$')

    async def test_heartbeat_requires_secret(self):
        self.assertEqual((await self.client.get('/sync/heartbeat')).status, 401)
        self.assertEqual((await self.client.get('/sync/heartbeat', headers={'Authorization': 'Bearer x'})).status, 401)


class TestChanges(SyncEndpointTestCase):
    async def test_paging_passthrough(self):
        for i in range(5):
            await self.state.set_user(str(i), {'i': i})
        resp = await self.client.get('/sync/changes?since=2&limit=2', headers=AUTH)
        page = await resp.json()
        self.assertEqual((page['since'], page['until'], page['has_more']), (2, 4, True))
        self.assertEqual([r[0] for r in page['rows']['users']], ['2', '3'])
        resp = await self.client.get('/sync/changes?since=4', headers=AUTH)
        page = await resp.json()
        self.assertEqual((page['until'], page['has_more']), (5, False))

    async def test_bad_params_are_400(self):
        for query in ('since=abc', 'limit=zero', 'since=-1', 'limit=0'):
            resp = await self.client.get(f'/sync/changes?{query}', headers=AUTH)
            self.assertEqual(resp.status, 400, query)

    async def test_limit_is_capped(self):
        with patch('dasovbot.dashboard.sync.read_changes', new_callable=AsyncMock) as mock_read:
            mock_read.return_value = {'since': 0, 'until': 0, 'has_more': False, 'rows': {}, 'tombstones': []}
            await self.client.get('/sync/changes?limit=99999', headers=AUTH)
        mock_read.assert_awaited_once_with(self.state.db, 0, 500)


class TestSnapshot(SyncEndpointTestCase):
    async def test_streams_a_readable_sqlite_file_and_cleans_up(self):
        await self.state.set_video('v1', VideoInfo(title='one'))
        await self.state.set_video('v2', VideoInfo(title='two'))
        tmp = tempfile.mkdtemp()
        known = os.path.join(tmp, 'snap.db')
        fd = os.open(known, os.O_CREAT | os.O_WRONLY)
        with patch('dasovbot.dashboard.sync.mkstemp', return_value=(fd, known)):
            resp = await self.client.get('/sync/snapshot', headers=AUTH)
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers['Content-Type'], 'application/x-sqlite3')
            self.assertEqual(resp.headers['X-Dasovbot-Rev'], '2')
            data = await resp.read()
        self.assertEqual(len(data), int(resp.headers['Content-Length']))
        self.assertFalse(os.path.exists(known), 'temp file removed after streaming')
        out = os.path.join(tmp, 'received.db')
        with open(out, 'wb') as f:
            f.write(data)
        with sqlite3.connect(out) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT value FROM sync_meta WHERE key='rev'").fetchone()[0], '2')

    async def test_snapshot_requires_secret(self):
        self.assertEqual((await self.client.get('/sync/snapshot')).status, 401)


class TestHandoff(SyncEndpointTestCase):
    controller = make_controller(role='active')

    async def test_active_accepts_and_drains(self):
        resp = await self.client.post('/sync/handoff', headers=AUTH, json={'manual': True, 'node': 'other'})
        self.assertEqual(resp.status, 202)
        self.assertEqual(await resp.json(), {'accepted': True, 'role': 'draining', 'drained': False})
        self.controller.handoff_requested_by_peer.assert_awaited_once_with(manual=True)

    async def test_missing_body_means_not_manual(self):
        self.controller.handoff_requested_by_peer.reset_mock()
        resp = await self.client.post('/sync/handoff', headers=AUTH)
        self.assertEqual(resp.status, 202)
        self.controller.handoff_requested_by_peer.assert_awaited_once_with(manual=False)

    async def test_already_draining_is_200_with_state(self):
        self.controller.role = 'draining'
        self.controller.handoff_requested_by_peer.return_value = {'accepted': True, 'role': 'draining', 'drained': True}
        try:
            resp = await self.client.post('/sync/handoff', headers=AUTH, json={})
            self.assertEqual(resp.status, 200)
            self.assertTrue((await resp.json())['drained'])
        finally:
            self.controller.role = 'active'
            self.controller.handoff_requested_by_peer.return_value = {'accepted': True, 'role': 'draining', 'drained': False}

    async def test_refused_is_409_with_reason(self):
        self.controller.handoff_requested_by_peer.side_effect = HaError('not active')
        try:
            resp = await self.client.post('/sync/handoff', headers=AUTH, json={'manual': False})
            self.assertEqual(resp.status, 409)
            self.assertEqual(await resp.json(), {'error': 'not active'})
        finally:
            self.controller.handoff_requested_by_peer.side_effect = None

    async def test_garbage_body_is_400(self):
        resp = await self.client.post('/sync/handoff', headers={**AUTH, 'Content-Type': 'application/json'},
                                      data='{not json')
        self.assertEqual(resp.status, 400)
