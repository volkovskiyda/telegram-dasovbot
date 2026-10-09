"""Sync client against a real peer node served by aiohttp's test server (plan item 08)."""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from dasovbot.dashboard.server import create_app
from dasovbot.database import get_meta, last_applied_key
from dasovbot.models import VideoInfo, Subscription
from dasovbot.services.sync import (
    SyncClient, SyncError, DeveloperNotifier, META_LAST_HEARTBEAT_AT, META_LAST_SYNC_AT, META_LAST_SYNC_REV,
    META_LAST_SNAPSHOT_AT,
)
from dasovbot.state import BotState
from tests.helpers import make_config, make_memory_db
from tests.test_sync_endpoints import make_controller

SECRET = 'shared-secret'


class SyncClientTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._env = patch.dict('os.environ', {'SYNC_SECRET': SECRET})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.peer_state = BotState(db=await make_memory_db(), config=make_config(node_name='other'))
        self.addAsyncCleanup(self.peer_state.db.close)
        self.peer_ctl = make_controller(role='active')
        self.server = TestServer(create_app(self.peer_state, self.peer_ctl))
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.local = BotState(db=await make_memory_db(), config=make_config(node_name='me'))
        self.addAsyncCleanup(self.local.db.close)
        self.t = 0.0
        self.client = self.make_client()
        self.addAsyncCleanup(self.client.aclose)

    def make_client(self, secret=SECRET, peer_url=None) -> SyncClient:
        config = make_config(node_name='me', node_role='standby', sync_secret=secret,
                             peer_url=peer_url or str(self.server.make_url('')).rstrip('/'))
        return SyncClient(config, self.local, clock=lambda: self.t)


class TestHeartbeat(SyncClientTestCase):
    async def test_success_persists_timestamp_and_peer_name(self):
        self.assertEqual(self.client.peer_name, '127.0.0.1')
        reply = await self.client.heartbeat()
        self.assertEqual(reply['role'], 'active')
        self.assertEqual(reply['node'], 'me' if False else self.peer_ctl.status()['node'])
        self.assertEqual(self.client.peer_name, self.peer_ctl.status()['node'])
        self.assertIsNotNone(self.client.last_heartbeat_at)
        self.assertEqual(await get_meta(self.local.db, META_LAST_HEARTBEAT_AT), self.client.last_heartbeat_at)
        self.assertEqual(self.client.sync_status()['last_heartbeat_at'], self.client.last_heartbeat_at)

    async def test_wrong_secret_returns_none(self):
        client = self.make_client(secret='wrong')
        self.addAsyncCleanup(client.aclose)
        self.assertIsNone(await client.heartbeat())
        self.assertIsNone(client.last_heartbeat_at)

    async def test_unreachable_peer_returns_none(self):
        client = self.make_client(peer_url='http://127.0.0.1:1')
        self.addAsyncCleanup(client.aclose)
        self.assertIsNone(await client.heartbeat())

    async def test_load_restores_persisted_values(self):
        await self.client.heartbeat()
        again = self.make_client()
        self.addAsyncCleanup(again.aclose)
        await again.load()
        self.assertEqual(again.last_heartbeat_at, self.client.last_heartbeat_at)


class TestPullChanges(SyncClientTestCase):
    async def test_walks_pages_and_persists_cursor(self):
        await self.local.set_video('seed', VideoInfo(title='no bootstrap'))
        for i in range(1200):
            await self.peer_state.set_user(str(i), {'i': i})
        await self.client.heartbeat()
        with patch('dasovbot.services.sync.MAX_PAGES_PER_TICK', 2):
            result = await self.client.pull_changes()
        self.assertEqual(result['applied'], 1000, 'two pages of 500 per tick')
        self.assertEqual(len(self.local.users), 1000)
        self.assertEqual(self.client.last_sync_rev, 1000)
        result = await self.client.pull_changes()
        self.assertEqual(result['applied'], 200)
        self.assertEqual(len(self.local.users), 1200)
        peer = self.client.peer_name
        self.assertEqual(await get_meta(self.local.db, last_applied_key(peer)), 1200)
        self.assertEqual(await get_meta(self.local.db, META_LAST_SYNC_REV), 1200)
        self.assertEqual(await get_meta(self.local.db, META_LAST_SYNC_AT), self.client.last_sync_at)
        self.assertEqual((await self.client.pull_changes())['applied'], 0)

    async def test_explicit_since_pulls_to_the_end_and_returns_touched(self):
        await self.local.set_video('seed', VideoInfo(title='not empty: no bootstrap'))
        for i in range(1100):
            await self.peer_state.set_video(f'v{i}', VideoInfo(title=str(i)))
        await self.client.heartbeat()
        with patch('dasovbot.services.sync.MAX_PAGES_PER_TICK', 1):
            result = await self.client.pull_changes(since=1000)
        self.assertEqual(len(result['touched']), 100)
        self.assertIn(('videos', 'v1099'), result['touched'])

    async def test_bootstrap_snapshot_when_local_is_empty(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.peer_state.set_subscription('s', Subscription(title='S'))
        await self.client.heartbeat()
        with patch.object(self.client, 'pull_snapshot', wraps=self.client.pull_snapshot) as spy:
            await self.client.pull_changes()
        spy.assert_awaited_once()
        self.assertEqual(self.local.videos['v'].title, 'T')
        self.assertEqual(self.local.subscriptions['s'].title, 'S')
        self.assertEqual(self.local.rev, self.peer_state.rev)
        # a second pull is incremental: no snapshot
        with patch.object(self.client, 'pull_snapshot', wraps=self.client.pull_snapshot) as spy:
            await self.client.pull_changes()
        spy.assert_not_awaited()

    async def test_explicit_since_on_an_empty_node_still_bootstraps(self):
        # The controller asks a never-synced node to reconcile from the peer's
        # handoff_rev: it must snapshot first, not take a partial feed
        for i in range(30):
            await self.peer_state.set_video(f'v{i}', VideoInfo(title=str(i)))
        await self.client.heartbeat()
        with patch.object(self.client, 'pull_snapshot', wraps=self.client.pull_snapshot) as spy:
            result = await self.client.pull_changes(since=28)
        spy.assert_awaited_once()
        self.assertEqual(len(self.local.videos), 30)
        self.assertEqual(result['touched'], set(), 'everything came via the snapshot, nothing after it')
        self.assertEqual(self.client.last_sync_rev, 30)

    async def test_upgraded_node_with_data_starts_incrementally(self):
        await self.local.set_video('old', VideoInfo(title='pre-HA'))
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.client.heartbeat()
        with patch.object(self.client, 'pull_snapshot', wraps=self.client.pull_snapshot) as spy:
            await self.client.pull_changes()
        spy.assert_not_awaited()
        self.assertIn('v', self.local.videos)

    async def test_http_error_raises_sync_error(self):
        await self.local.set_video('x', VideoInfo(title='x'))  # no bootstrap
        client = self.make_client(secret='wrong')
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(SyncError) as ctx:
            await client.pull_changes()
        self.assertIn('401', str(ctx.exception))
        self.assertIsNone(client.last_sync_at, 'failure leaves readiness untouched')

    async def test_connection_error_raises_sync_error(self):
        await self.local.set_video('x', VideoInfo(title='x'))
        client = self.make_client(peer_url='http://127.0.0.1:1')
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(SyncError):
            await client.pull_changes()


class TestSnapshot(SyncClientTestCase):
    async def test_snapshot_reconciles_and_removes_temp_file(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.local.set_video('stale', VideoInfo(title='old'))
        tmp = tempfile.mkdtemp()
        known = os.path.join(tmp, 'snap.db')
        fd = os.open(known, os.O_CREAT | os.O_WRONLY)
        await self.client.heartbeat()
        with patch('dasovbot.services.sync.mkstemp', return_value=(fd, known)):
            await self.client.pull_snapshot()
        self.assertFalse(os.path.exists(known))
        self.assertEqual(set(self.local.videos), {'v'})
        self.assertEqual(self.client.last_sync_rev, self.peer_state.rev)
        self.assertEqual(await get_meta(self.local.db, META_LAST_SNAPSHOT_AT), self.client.last_snapshot_at)
        self.assertEqual(self.client.last_sync_at, self.client.last_snapshot_at)

    async def test_failed_download_raises_and_removes_temp_file(self):
        tmp = tempfile.mkdtemp()
        known = os.path.join(tmp, 'snap.db')
        fd = os.open(known, os.O_CREAT | os.O_WRONLY)
        client = self.make_client(secret='wrong')
        self.addAsyncCleanup(client.aclose)
        with patch('dasovbot.services.sync.mkstemp', return_value=(fd, known)):
            with self.assertRaises(SyncError):
                await client.pull_snapshot()
        self.assertFalse(os.path.exists(known))
        self.assertIsNone(client.last_snapshot_at)

    async def test_maybe_snapshot_arms_then_pulls_hourly(self):
        await self.peer_state.set_video('v', VideoInfo(title='T'))
        await self.client.heartbeat()
        with patch.object(self.client, 'pull_snapshot', new_callable=AsyncMock) as spy:
            await self.client.maybe_snapshot()      # arms the timer only
            self.t += 3599
            await self.client.maybe_snapshot()
            spy.assert_not_awaited()
            self.t += 1
            await self.client.maybe_snapshot()
            spy.assert_awaited_once()


class TestHandoffAndHandback(SyncClientTestCase):
    async def test_request_handoff_true_on_202(self):
        self.assertTrue(await self.client.request_handoff(manual=True))
        self.peer_ctl.handoff_requested_by_peer.assert_awaited_with(manual=True)

    async def test_request_handoff_false_on_409(self):
        from dasovbot.services.ha import HaError
        self.peer_ctl.handoff_requested_by_peer.side_effect = HaError('not active')
        try:
            self.assertFalse(await self.client.request_handoff(manual=False))
        finally:
            self.peer_ctl.handoff_requested_by_peer.side_effect = None

    async def test_request_handoff_false_when_unreachable(self):
        client = self.make_client(peer_url='http://127.0.0.1:1')
        self.addAsyncCleanup(client.aclose)
        self.assertFalse(await client.request_handoff(manual=True))

    async def test_on_handed_back_raises_cursor_and_marks_fresh(self):
        await self.client.heartbeat()
        self.local.rev = 500
        await self.client.on_handed_back()
        self.assertEqual(await get_meta(self.local.db, last_applied_key(self.client.peer_name)), 500)
        self.assertEqual(self.client.last_sync_rev, 500)
        self.assertIsNotNone(self.client.last_sync_at)
        self.assertEqual(await get_meta(self.local.db, META_LAST_SYNC_AT), self.client.last_sync_at)

    async def test_aclose_closes_session(self):
        await self.client.heartbeat()
        session = self.client._session
        self.assertFalse(session.closed)
        await self.client.aclose()
        self.assertTrue(session.closed)
        self.assertIsNotNone(await self.client.heartbeat(), 'a new session is opened on demand')


class TestDeveloperNotifier(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.t = 0.0
        self.notifier = DeveloperNotifier(make_config(developer_chat_id='42'), clock=lambda: self.t)
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()

    async def test_without_bot_only_logs(self):
        with self.assertLogs('dasovbot.services.sync', level='INFO') as cm:
            await self.notifier.transition('🟢 me is now ACTIVE')
        self.assertIn('ACTIVE', '\n'.join(cm.output))

    async def test_transitions_always_sent(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.transition('one')
        await self.notifier.transition('two')
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.bot.send_message.assert_awaited_with(chat_id='42', text='two')

    async def test_identical_transition_collapsed_within_window(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.transition('flap')
        self.t += 60
        await self.notifier.transition('flap')
        self.t += 60
        await self.notifier.transition('other')
        self.assertEqual([c.kwargs['text'] for c in self.bot.send_message.await_args_list], ['flap', 'other'])

    async def test_collapsed_count_reported_once_the_window_passes(self):
        self.notifier.attach_bot(self.bot)
        for _ in range(4):
            await self.notifier.transition('flap')
            self.t += 120
        self.t += 600
        await self.notifier.transition('flap')
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.bot.send_message.assert_awaited_with(chat_id='42', text='flap (+3 collapsed in the last 18 min)')
        await self.notifier.transition('flap')       # a fresh window: collapsed again, count restarts
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_errors_rate_limited_to_one_per_interval(self):
        self.notifier.attach_bot(self.bot)
        await self.notifier.error('a')
        self.t += 599
        await self.notifier.error('b')
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.t += 1
        await self.notifier.error('c')
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.bot.send_message.assert_awaited_with(chat_id='42', text='c')

    async def test_send_failure_is_swallowed(self):
        self.notifier.attach_bot(self.bot)
        self.bot.send_message.side_effect = RuntimeError('telegram down')
        await self.notifier.transition('x')  # no raise
