"""Role controller state machine (plan item 06): fake clock, peer, runner and notifier."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from dasovbot.constants import HA_ROLE_ACTIVE, HA_ROLE_DRAINING, HA_ROLE_PASSIVE
from dasovbot.database import get_meta
from dasovbot.services.ha import (
    RoleController, HaError, SyncError, META_HANDOFF_REV, META_MANUAL_HOLD,
)
from tests.helpers import make_config, make_memory_db, make_state

HEARTBEAT, TTL, STABLE = 10.0, 30.0, 180.0
T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


class FakePeer:
    def __init__(self):
        self.reply = None                 # dict | None | callable
        self.handoff_ok = True
        self.handoff_calls = []
        self.pull_calls = []
        self.pull_error = None            # exception to raise from pull_changes
        self.touched = set()
        self.status = {'last_sync_at': '20261007_120000', 'last_heartbeat_at': '20261007_120005',
                       'last_sync_rev': 10, 'peer': 'other'}
        self.handed_back = 0
        self.snapshots = 0

    async def heartbeat(self):
        return self.reply() if callable(self.reply) else self.reply

    async def request_handoff(self, manual):
        self.handoff_calls.append(manual)
        return self.handoff_ok

    async def pull_changes(self, since=None):
        self.pull_calls.append(since)
        if self.pull_error:
            raise self.pull_error
        return {'applied': 0, 'touched': set(self.touched)}

    async def pull_snapshot(self):
        self.snapshots += 1

    async def on_handed_back(self):
        self.handed_back += 1

    def sync_status(self):
        return dict(self.status)


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.drain_gate = None            # asyncio.Event the drain waits for, if set

    async def start(self, backlog_since):
        self.calls.append(('start', backlog_since))

    async def stop_polling(self):
        self.calls.append(('stop_polling',))

    async def drain(self):
        self.calls.append(('drain',))
        if self.drain_gate is not None:
            await self.drain_gate.wait()

    async def stop_all(self):
        self.calls.append(('stop_all',))

    def names(self):
        return [c[0] for c in self.calls]


class FakeNotifier:
    def __init__(self):
        self.transitions, self.errors = [], []

    async def transition(self, text):
        self.transitions.append(text)

    async def error(self, text):
        self.errors.append(text)


class ControllerTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.t = 0.0
        self.wall = T0
        self.peer = FakePeer()
        self.runner = FakeRunner()
        self.notifier = FakeNotifier()
        self.state = make_state(db=await make_memory_db(), rev=100)

    async def asyncTearDown(self):
        await self.state.db.close()

    def make(self, role='standby', **overrides) -> RoleController:
        settings = dict(node_role=role, node_name='me', peer_url='http://peer:8080', sync_secret='s',
                        heartbeat_interval_sec=HEARTBEAT, lease_ttl_sec=TTL, failback_stable_sec=STABLE)
        settings.update(overrides)
        config = make_config(**settings)
        return RoleController(config, self.state, self.peer, self.runner, self.notifier,
                              clock=lambda: self.t, wall_clock=lambda: self.wall)

    async def started(self, role='standby', **overrides) -> RoleController:
        ctl = self.make(role, **overrides)
        await ctl.start()
        self.addAsyncCleanup(ctl.stop)
        return ctl

    async def tick(self, ctl, advance=HEARTBEAT):
        self.t += advance
        self.wall += timedelta(seconds=advance)
        await ctl.tick()

    def active_reply(self, **extra):
        return {'node': 'other', 'role': HA_ROLE_ACTIVE, 'rev': 100, 'drained': False, **extra}


class TestStandalone(ControllerTestCase):
    async def test_active_at_start_without_loop(self):
        config = make_config(node_name='solo')
        ctl = RoleController(config, self.state, None, self.runner, self.notifier, clock=lambda: self.t)
        await ctl.start()
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertEqual(self.runner.calls, [('start', None)])
        self.assertIsNone(ctl._task)
        self.assertTrue(ctl.is_ready())
        status = ctl.status()
        self.assertEqual((status['role'], status['lease_holder'], status['enabled']), ('active', 'solo', False))
        with self.assertRaises(HaError):
            await ctl.request_takeover()
        await ctl.stop()
        self.assertEqual(self.runner.names(), ['start', 'stop_all'])


class TestColdStart(ControllerTestCase):
    async def test_primary_alone_claims_after_one_probe(self):
        self.peer.reply = None
        ctl = await self.started('primary')
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        await self.tick(ctl, 5)
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        await self.tick(ctl, 5)                      # t=10
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertEqual(self.runner.calls, [('start', None)], 'never-seen peer: keep the whole backlog')
        self.assertIn('cold start', self.notifier.transitions[-1])
        self.assertEqual(await get_meta(self.state.db, META_HANDOFF_REV), 100)

    async def test_standby_alone_waits_ttl_plus_probe(self):
        self.peer.reply = None
        ctl = await self.started('standby')
        await self.tick(ctl, 30)
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        await self.tick(ctl, 10)                     # t=40
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)

    async def test_standby_alone_not_ready_stays_passive_and_retries(self):
        self.peer.reply = None
        self.peer.status['last_sync_at'] = None
        ctl = await self.started('standby')
        await self.tick(ctl, 40)
        await self.tick(ctl, 10)
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        self.assertEqual(len(self.notifier.errors), 2)
        self.assertIn('never synced', self.notifier.errors[0])
        self.peer.status['last_sync_at'] = '20261007_120000'
        await self.tick(ctl, 10)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)


class TestPassiveFollowing(ControllerTestCase):
    async def test_pulls_each_tick_while_peer_active(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('standby')
        for _ in range(3):
            await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        self.assertEqual(self.peer.pull_calls, [None, None, None])
        self.assertEqual(self.peer.handoff_calls, [])
        status = ctl.status()
        self.assertEqual((status['lease_holder'], status['peer_role']), ('other', 'active'))

    async def test_sync_error_is_reported_and_loop_continues(self):
        self.peer.reply = self.active_reply()
        self.peer.pull_error = SyncError('boom')
        ctl = await self.started('standby')
        await self.tick(ctl)
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        self.assertEqual(len(self.notifier.errors), 2)
        self.assertIn('boom', self.notifier.errors[0])

    async def test_returning_node_reconciles_once_per_handoff(self):
        self.peer.reply = self.active_reply(handoff_rev=90)
        self.peer.touched = {('videos', 'B')}
        ctl = await self.started('primary')
        await self.tick(ctl)
        self.assertEqual(self.peer.pull_calls, [90], 'reconcile pulls from the peer handoff point')
        await self.tick(ctl)
        self.assertEqual(self.peer.pull_calls, [90, None], 'then the normal incremental pull')
        self.peer.reply = self.active_reply(handoff_rev=95)
        await self.tick(ctl)
        self.assertEqual(self.peer.pull_calls, [90, None, 95], 'a new handoff point reconciles again')


class TestLeaseLost(ControllerTestCase):
    async def test_standby_takes_over_after_ttl(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('standby')
        await self.tick(ctl)                         # t=10, active seen
        self.peer.reply = None
        await self.tick(ctl)                         # t=20
        await self.tick(ctl)                         # t=30
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        await self.tick(ctl)                         # t=40 = 30 s since last ACTIVE reply
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertIn('lease lost', self.notifier.transitions[-1])
        self.assertEqual(self.runner.calls, [('start', T0 + timedelta(seconds=10) - timedelta(seconds=HEARTBEAT))])
        self.assertFalse(ctl.manual_hold, 'automatic takeover sets no hold')
        self.assertEqual(ctl.status()['lease_holder'], 'me')

    async def test_draining_reply_does_not_move_backlog_cutoff(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('standby')
        await self.tick(ctl)                         # t=10 ACTIVE seen at wall T0+10
        self.peer.reply = {**self.active_reply(), 'role': HA_ROLE_DRAINING}
        await self.tick(ctl)                         # t=20 DRAINING: lease still held, cutoff unchanged
        self.peer.reply = None
        for _ in range(3):
            await self.tick(ctl)                     # t=50 ≥ 20+30
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertEqual(self.runner.calls[0][1], T0 + timedelta(seconds=10) - timedelta(seconds=HEARTBEAT))


class TestFailback(ControllerTestCase):
    async def test_primary_requests_handoff_after_stability_window(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('primary')
        for _ in range(17):
            await self.tick(ctl)                     # healthy since t=10, now t=170 → 160 s
        self.assertEqual(self.peer.handoff_calls, [])
        await self.tick(ctl)                         # t=180 → 170 s
        self.assertEqual(self.peer.handoff_calls, [])
        await self.tick(ctl)                         # t=190 → 180 s
        self.assertEqual(self.peer.handoff_calls, [False])
        self.assertTrue(ctl.handoff_pending)
        self.assertIn('requested handoff', self.notifier.transitions[-1])
        await self.tick(ctl)
        self.assertEqual(self.peer.handoff_calls, [False], 'requested once')

    async def test_failure_resets_stability_window(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('primary')
        for _ in range(10):
            await self.tick(ctl)                     # healthy 10..110
        self.peer.reply = None
        await self.tick(ctl)                         # t=120 failure
        self.peer.reply = self.active_reply()
        for _ in range(18):
            await self.tick(ctl)                     # healthy from t=120 → 290 - 120 = 170: not yet
        self.assertEqual(self.peer.handoff_calls, [])
        await self.tick(ctl)                         # t=300 → 180
        self.assertEqual(self.peer.handoff_calls, [False])

    async def test_manual_hold_in_reply_suppresses_failback(self):
        self.peer.reply = self.active_reply(manual_hold=True)
        ctl = await self.started('primary')
        for _ in range(30):
            await self.tick(ctl)
        self.assertEqual(self.peer.handoff_calls, [])

    async def test_handback_requested_triggers_immediately_even_for_standby(self):
        self.peer.reply = self.active_reply(handback_requested=True)
        ctl = await self.started('standby')
        await self.tick(ctl)
        self.assertEqual(self.peer.handoff_calls, [True])

    async def test_standby_never_auto_requests(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('standby')
        for _ in range(30):
            await self.tick(ctl)
        self.assertEqual(self.peer.handoff_calls, [])

    async def test_not_ready_blocks_request(self):
        self.peer.reply = self.active_reply(handback_requested=True)
        self.peer.status['last_sync_at'] = '20261007_100000'   # 2 h behind the heartbeat
        ctl = await self.started('primary')
        await self.tick(ctl)
        self.assertEqual(self.peer.handoff_calls, [])
        self.assertFalse(ctl.is_ready())


class TestHandoffRequestingSide(ControllerTestCase):
    async def test_drained_reply_final_pull_then_active(self):
        self.peer.reply = self.active_reply(handback_requested=True)
        ctl = await self.started('standby')
        await self.tick(ctl)                                       # requests handoff
        self.assertTrue(ctl.handoff_pending)
        self.peer.reply = {**self.active_reply(), 'role': HA_ROLE_DRAINING, 'drained': False}
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        self.peer.reply = {**self.active_reply(), 'role': HA_ROLE_DRAINING, 'drained': True}
        pulls_before = len(self.peer.pull_calls)
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertEqual(len(self.peer.pull_calls), pulls_before + 2, 'regular pull + final pull')
        self.assertFalse(ctl.handoff_pending)
        self.assertTrue(ctl.manual_hold, 'a handback-driven takeover holds until Hand back')
        self.assertEqual(await get_meta(self.state.db, META_MANUAL_HOLD), True)

    async def test_final_pull_error_keeps_passive_and_retries(self):
        self.peer.reply = self.active_reply(handback_requested=True)
        ctl = await self.started('standby')
        await self.tick(ctl)
        self.peer.reply = {**self.active_reply(), 'role': HA_ROLE_DRAINING, 'drained': True}
        self.peer.pull_error = SyncError('peer gone mid-handoff')
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        self.assertTrue(ctl.handoff_pending)
        self.assertTrue(any('final pull' in e for e in self.notifier.errors))
        self.peer.pull_error = None
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)

    async def test_rejected_handoff_request_is_retried_next_tick(self):
        self.peer.reply = self.active_reply(handback_requested=True)
        self.peer.handoff_ok = False
        ctl = await self.started('standby')
        await self.tick(ctl)
        await self.tick(ctl)
        self.assertEqual(self.peer.handoff_calls, [True, True])
        self.assertFalse(ctl.handoff_pending)


class TestHandoffDrainingSide(ControllerTestCase):
    async def activate(self, role='primary'):
        self.peer.reply = None
        ctl = await self.started(role)
        await self.tick(ctl, 40)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.runner.calls.clear()
        return ctl

    async def test_handoff_stops_polling_inline_then_drains(self):
        ctl = await self.activate()
        self.runner.drain_gate = asyncio.Event()
        result = await ctl.handoff_requested_by_peer(manual=True)
        self.assertEqual(result, {'accepted': True, 'role': HA_ROLE_DRAINING, 'drained': False})
        self.assertEqual(self.runner.names(), ['stop_polling'])
        await asyncio.sleep(0)
        self.assertEqual(self.runner.names(), ['stop_polling', 'drain'])
        self.assertFalse(ctl.drained)
        again = await ctl.handoff_requested_by_peer(manual=True)
        self.assertEqual(again['drained'], False)
        self.runner.drain_gate.set()
        await asyncio.sleep(0)
        self.assertTrue(ctl.drained)
        self.assertIn('drained', self.notifier.transitions[-1])
        self.assertEqual((await ctl.handoff_requested_by_peer(manual=False))['drained'], True)
        # peer takes over → PASSIVE
        self.peer.reply = self.active_reply()
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        self.assertIsNone(ctl.handoff_rev)
        self.assertIsNone(await get_meta(self.state.db, META_HANDOFF_REV))
        self.assertEqual(self.peer.handed_back, 1)
        self.assertIn('handed over', self.notifier.transitions[-1])

    async def test_peer_never_takes_over_reactivates_after_ttl(self):
        ctl = await self.activate()
        await ctl.handoff_requested_by_peer(manual=True)
        await asyncio.sleep(0)
        self.assertTrue(ctl.drained)
        self.peer.reply = {'node': 'other', 'role': HA_ROLE_PASSIVE}
        await self.tick(ctl, 20)
        self.assertEqual(ctl.role, HA_ROLE_DRAINING)
        await self.tick(ctl, 10)                     # 30 s after the drain finished
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertIn('never took over', self.notifier.transitions[-1])
        self.assertEqual(self.runner.names()[-1], 'start')

    async def test_primary_refuses_automatic_handoff(self):
        ctl = await self.activate('primary')
        with self.assertRaises(HaError):
            await ctl.handoff_requested_by_peer(manual=False)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)

    async def test_standby_accepts_automatic_handoff(self):
        ctl = await self.activate('standby')
        result = await ctl.handoff_requested_by_peer(manual=False)
        self.assertTrue(result['accepted'])
        self.assertEqual(ctl.role, HA_ROLE_DRAINING)

    async def test_passive_rejects_handoff(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('standby')
        with self.assertRaises(HaError):
            await ctl.handoff_requested_by_peer(manual=True)


class TestConflictAndTwoActives(ControllerTestCase):
    async def activate(self, role):
        self.peer.reply = None
        ctl = await self.started(role)
        await self.tick(ctl, 40)
        self.runner.calls.clear()
        return ctl

    async def test_409_standby_steps_down(self):
        ctl = await self.activate('standby')
        ctl.on_conflict()
        ctl.on_conflict()                            # idempotent while stepping down
        await asyncio.sleep(0.02)                    # the step-down task persists + notifies
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        self.assertEqual(self.runner.names(), ['stop_all'])
        self.assertEqual(self.peer.handed_back, 1)
        self.assertEqual(sum('stepped down' in t for t in self.notifier.transitions), 1)

    async def test_409_primary_keeps_polling(self):
        ctl = await self.activate('primary')
        ctl.on_conflict()
        await asyncio.sleep(0)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertEqual(self.runner.calls, [])
        self.assertTrue(any('409' in e for e in self.notifier.errors))

    async def test_two_actives_standby_yields_primary_stays(self):
        standby = await self.activate('standby')
        self.peer.reply = self.active_reply()
        await self.tick(standby)
        self.assertEqual(standby.role, HA_ROLE_PASSIVE)
        self.assertEqual(self.runner.names(), ['stop_all'])

        self.runner.calls.clear()
        primary = await self.activate('primary')
        self.peer.reply = self.active_reply()
        await self.tick(primary)
        self.assertEqual(primary.role, HA_ROLE_ACTIVE)
        self.assertEqual(self.runner.calls, [])
        self.assertTrue(any('both nodes' in e for e in self.notifier.errors))


class TestManualOverrides(ControllerTestCase):
    async def test_takeover_not_ready_raises(self):
        self.peer.reply = self.active_reply()
        self.peer.status['last_sync_at'] = None
        ctl = await self.started('standby')
        with self.assertRaises(HaError) as ctx:
            await ctl.request_takeover()
        self.assertIn('not ready', str(ctx.exception))

    async def test_takeover_with_active_peer_posts_manual_handoff_and_holds(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('standby')
        await ctl.request_takeover()
        await self.tick(ctl)
        self.assertEqual(self.peer.handoff_calls, [True])
        self.peer.reply = {**self.active_reply(), 'role': HA_ROLE_DRAINING, 'drained': True}
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertTrue(ctl.manual_hold)
        self.assertIn('handoff', self.notifier.transitions[-1])

    async def test_takeover_without_peer_activates_at_once(self):
        self.peer.reply = None
        ctl = await self.started('standby')
        await self.tick(ctl)                         # t=10: far from the 40 s cold-start wait
        self.assertEqual(ctl.role, HA_ROLE_PASSIVE)
        await ctl.request_takeover()
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertIn('manual takeover', self.notifier.transitions[-1])

    async def test_primary_manual_takeover_sets_no_hold(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('primary')
        await ctl.request_takeover()
        await self.tick(ctl)
        self.peer.reply = {**self.active_reply(), 'role': HA_ROLE_DRAINING, 'drained': True}
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertFalse(ctl.manual_hold)

    async def test_handback_clears_hold_and_exports_flag(self):
        self.peer.reply = None
        ctl = await self.started('standby')
        await ctl.request_takeover()
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        self.assertTrue(ctl.manual_hold)
        await ctl.request_handback()
        self.assertFalse(ctl.manual_hold)
        self.assertEqual(await get_meta(self.state.db, META_MANUAL_HOLD), False)
        self.assertTrue(ctl.status()['handback_requested'])
        with self.assertRaises(HaError):
            await ctl.request_takeover()

    async def test_handback_on_passive_raises(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('standby')
        with self.assertRaises(HaError):
            await ctl.request_handback()

    async def test_persisted_hold_and_handoff_rev_survive_restart(self):
        self.peer.reply = None
        ctl = self.make('standby')
        await ctl.start()
        await ctl.request_takeover()
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        await ctl.stop()
        self.state.rev = 150                         # wrote more while active, then restarted
        again = self.make('standby')
        await again.start()
        self.addAsyncCleanup(again.stop)
        self.assertTrue(again.manual_hold)
        self.assertEqual(again.handoff_rev, 100)
        await self.tick(again, 40)
        self.assertEqual(again.role, HA_ROLE_ACTIVE)
        self.assertEqual(again.handoff_rev, 100, 'the original handoff point is kept across a restart')


class TestStop(ControllerTestCase):
    async def test_stop_while_active_stops_all(self):
        self.peer.reply = None
        ctl = await self.started('primary')
        await self.tick(ctl)
        self.assertEqual(ctl.role, HA_ROLE_ACTIVE)
        await ctl.stop()
        self.assertEqual(self.runner.names(), ['start', 'stop_all'])

    async def test_stop_while_passive_does_not(self):
        self.peer.reply = self.active_reply()
        ctl = await self.started('standby')
        await self.tick(ctl)
        await ctl.stop()
        self.assertEqual(self.runner.calls, [])

    async def test_loop_survives_tick_exception(self):
        async def boom():
            raise RuntimeError('tick exploded')
        ctl = await self.started('standby', heartbeat_interval_sec=0.01, lease_ttl_sec=0.02)
        ctl.tick = boom
        await asyncio.sleep(0.05)
        self.assertFalse(ctl._task.done())
        self.assertTrue(any('tick exploded' in e for e in self.notifier.errors))
