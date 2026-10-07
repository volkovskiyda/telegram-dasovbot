"""Role controller: active/standby failover between two nodes.

The process always runs the dashboard and the sync endpoints; polling and the
background tasks run only while this node is ACTIVE. The controller decides
the role from a heartbeat/lease against the peer:

    PASSIVE --lease lost / cold start / handoff drained--> ACTIVE
    ACTIVE  --handoff requested by the peer--> DRAINING --peer ACTIVE--> PASSIVE

Everything that touches Telegram, the peer or the dashboard goes through the
three ports below (implemented in services/sync.py and __main__.py), so the
machine itself is plain logic driven by `tick()` on a monotonic clock.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol

from dasovbot.config import Config
from dasovbot.constants import (
    DATETIME_FORMAT, HA_ROLE_ACTIVE, HA_ROLE_DRAINING, HA_ROLE_PASSIVE, READINESS_LEASE_FACTOR,
)
from dasovbot.database import get_meta, set_meta
from dasovbot.state import BotState

logger = logging.getLogger(__name__)

META_MANUAL_HOLD = 'ha:manual_hold'
META_HANDOFF_REV = 'ha:handoff_rev'


class HaError(Exception):
    """A role transition that cannot be honoured right now (message is user-facing)."""


class SyncError(Exception):
    """Pulling the peer's data failed; the caller keeps the node PASSIVE."""


class PeerClient(Protocol):
    async def heartbeat(self) -> dict | None:
        """The peer's /sync/heartbeat reply, or None on any failure (already logged)."""

    async def request_handoff(self, manual: bool) -> bool: ...

    async def pull_changes(self, since: int | None = None) -> dict:
        """Pull and apply the peer's feed; with an explicit `since`, to the end.

        Returns {'applied': rows, 'touched': set of (table, key)}. Raises SyncError.
        """

    async def pull_snapshot(self) -> None: ...

    async def on_handed_back(self) -> None:
        """This node just left ACTIVE: its data is current, move the cursors accordingly."""

    def sync_status(self) -> dict:
        """{'last_sync_at', 'last_heartbeat_at', 'last_sync_rev', 'peer'} (DATETIME_FORMAT strings)."""


class ActiveRunner(Protocol):
    async def start(self, backlog_since: datetime | None) -> None:
        """Drain and date-filter the Bot API backlog, then start polling and background tasks."""

    async def stop_polling(self) -> None: ...

    async def drain(self) -> None:
        """Stop new work, wait for the in-flight download/upload, stop the worker."""

    async def stop_all(self) -> None:
        """Stop polling and every background task without waiting."""


class Notifier(Protocol):
    async def transition(self, text: str) -> None: ...

    async def error(self, text: str) -> None:
        """Rate-limited to one message per SYNC_ERROR_NOTIFY_INTERVAL_SEC."""


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, DATETIME_FORMAT)
    except ValueError:
        return None


class RoleController:
    def __init__(self, config: Config, state: BotState, peer: PeerClient | None, runner: ActiveRunner | None,
                 notifier: Notifier, clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.config = config
        self.state = state
        self.peer = peer
        self.runner = runner
        self.notifier = notifier
        self.clock = clock
        self.wall_clock = wall_clock

        self.role = HA_ROLE_PASSIVE
        self.started_at: float | None = None
        self.last_active_seen: float | None = None    # monotonic: last reply with role ACTIVE/DRAINING
        self.peer_active_wall_at: datetime | None = None  # wall clock: last reply with role ACTIVE (decision 18)
        self.last_ok_at: float | None = None
        self.healthy_since: float | None = None
        self.peer_status: dict | None = None
        self.handoff_pending = False
        self.drained = False
        self.drain_finished_at: float | None = None
        self.drain_task: asyncio.Task | None = None
        self.manual_hold = False                      # persisted
        self.handback_requested = False
        self.handoff_rev: int | None = None           # persisted
        self._manual_takeover = False
        self._handoff_manual = False       # the pending handoff was manual / a handback
        self._reconciled_for: tuple[str, int] | None = None
        self._stepping_down = False
        self._task: asyncio.Task | None = None

    # --- public API ---------------------------------------------------------

    @property
    def node(self) -> str:
        return self.config.node_name

    @property
    def peer_name(self) -> str:
        if self.peer_status and self.peer_status.get('node'):
            return self.peer_status['node']
        if self.peer is not None:
            name = self.peer.sync_status().get('peer')
            if name:
                return name
        return self.config.peer_url or 'peer'

    @property
    def is_active(self) -> bool:
        return self.role == HA_ROLE_ACTIVE

    def attach_runner(self, runner: ActiveRunner):
        self.runner = runner

    async def start(self):
        self.started_at = self.clock()
        if not self.config.ha_enabled:
            self.role = HA_ROLE_ACTIVE
            await self.runner.start(backlog_since=None)
            logger.info("role: active (standalone)")
            return
        self.manual_hold = bool(await get_meta(self.state.db, META_MANUAL_HOLD, False))
        self.handoff_rev = await get_meta(self.state.db, META_HANDOFF_REV, None)
        self.role = HA_ROLE_PASSIVE
        logger.info("role: passive (%s, peer %s)", self.config.node_role, self.config.peer_url)
        self._task = asyncio.create_task(self.run(), name='ha_controller')

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.drain_task and not self.drain_task.done():
            self.drain_task.cancel()
        if self.role in (HA_ROLE_ACTIVE, HA_ROLE_DRAINING):
            # A restart is not a handoff: no drain, the lease covers the gap
            await self.runner.stop_all()

    async def run(self):
        # Sleep first: the node starts PASSIVE and the first probe happens one
        # heartbeat interval later (the cold-start rule counts from started_at)
        while True:
            await asyncio.sleep(self.config.heartbeat_interval_sec)
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001 — the loop must survive anything
                logger.error("HA tick failed", exc_info=True)
                await self.notifier.error(f"⚠️ {self.node} HA loop error: {e}")

    def status(self) -> dict:
        sync = self.peer.sync_status() if self.peer is not None else {}
        active_here = self.role in (HA_ROLE_ACTIVE, HA_ROLE_DRAINING)
        peer_active = self._peer_active()
        lease_until = None
        if active_here:
            lease_until = (datetime.now() + timedelta(seconds=self.config.lease_ttl_sec)).strftime(DATETIME_FORMAT)
        return {
            'enabled': self.config.ha_enabled,
            'role': self.role,
            'node': self.node,
            'node_role': self.config.node_role,
            'peer': self.peer_name if self.config.ha_enabled else None,
            'peer_url': self.config.peer_url,
            'lease_holder': self.node if active_here else (self.peer_name if peer_active else None),
            'lease_until': lease_until,
            'rev': self.state.rev,
            'drained': self.drained,
            'manual_hold': self.manual_hold,
            'handback_requested': self.handback_requested,
            'handoff_rev': self.handoff_rev,
            'handoff_pending': self.handoff_pending,
            'peer_role': self.peer_status.get('role') if self.peer_status else None,
            'peer_seen_at': self._peer_seen_at(),
            'ready': self.is_ready(),
            'last_sync_at': sync.get('last_sync_at'),
            'last_sync_rev': sync.get('last_sync_rev'),
            'last_heartbeat_at': sync.get('last_heartbeat_at'),
            'last_snapshot_at': sync.get('last_snapshot_at'),
        }

    def is_ready(self) -> bool:
        """Fresh enough to take over: last sync within READINESS_LEASE_FACTOR lease TTLs of the last heartbeat."""
        if not self.config.ha_enabled:
            return True
        if self.role in (HA_ROLE_ACTIVE, HA_ROLE_DRAINING):
            return True
        sync = self.peer.sync_status()
        heartbeat_at, sync_at = _parse(sync.get('last_heartbeat_at')), _parse(sync.get('last_sync_at'))
        if heartbeat_at is None or sync_at is None:
            return False
        return (heartbeat_at - sync_at).total_seconds() <= READINESS_LEASE_FACTOR * self.config.lease_ttl_sec

    def readiness_reason(self) -> str:
        sync = self.peer.sync_status() if self.peer is not None else {}
        if not sync.get('last_sync_at'):
            return 'never synced from the peer'
        if not sync.get('last_heartbeat_at'):
            return 'no heartbeat from the peer yet'
        heartbeat_at, sync_at = _parse(sync.get('last_heartbeat_at')), _parse(sync.get('last_sync_at'))
        if heartbeat_at is None or sync_at is None:
            return 'unreadable sync timestamps'
        lag = int((heartbeat_at - sync_at).total_seconds())
        return f'last sync {lag}s behind the last heartbeat'

    # Manual overrides (dashboard) --------------------------------------------

    async def request_takeover(self):
        """Dashboard "Take over" on a PASSIVE node: force the transition, readiness still enforced."""
        if not self.config.ha_enabled:
            raise HaError('HA is not enabled on this node')
        if self.role != HA_ROLE_PASSIVE:
            raise HaError('already active')
        if not self.is_ready():
            raise HaError(f'not ready: {self.readiness_reason()}')
        self._manual_takeover = True

    async def request_handback(self):
        """Dashboard "Hand back" on an ACTIVE node: the peer is invited to take over."""
        if not self.config.ha_enabled:
            raise HaError('HA is not enabled on this node')
        if self.role != HA_ROLE_ACTIVE:
            raise HaError('not active')
        if self.manual_hold:
            self.manual_hold = False
            await self._persist(META_MANUAL_HOLD, False)
        self.handback_requested = True

    async def handoff_requested_by_peer(self, manual: bool) -> dict:
        """POST /sync/handoff: the passive peer wants the lease. Drains inline when ACTIVE."""
        if self.role == HA_ROLE_DRAINING:
            return {'accepted': True, 'role': self.role, 'drained': self.drained}
        if self.role != HA_ROLE_ACTIVE:
            raise HaError('not active')
        if self.config.is_primary and not manual:
            raise HaError('the primary only yields to a manual handoff')
        await self._start_drain()
        return {'accepted': True, 'role': self.role, 'drained': False}

    def on_conflict(self):
        """Telegram 409 Conflict on getUpdates while ACTIVE (from the PTB error handler)."""
        if self.role != HA_ROLE_ACTIVE:
            return
        if self.config.is_primary:
            logger.warning("409 Conflict while ACTIVE on the primary; the standby should yield")
            asyncio.create_task(self.notifier.error(f"⚠️ {self.node} got 409 Conflict while ACTIVE — the standby should yield"))
            return
        if self._stepping_down:
            return
        self._stepping_down = True
        asyncio.create_task(self._step_down('Telegram 409 Conflict'))

    # --- one heartbeat cycle ------------------------------------------------

    async def tick(self):
        reply = await self.peer.heartbeat()
        now = self.clock()
        if reply:
            self.last_ok_at = now
            self.healthy_since = self.healthy_since or now
            self.peer_status = reply
            if reply.get('role') in (HA_ROLE_ACTIVE, HA_ROLE_DRAINING):
                self.last_active_seen = now
            if reply.get('role') == HA_ROLE_ACTIVE:
                self.peer_active_wall_at = self.wall_clock()
        else:
            self.healthy_since = None
            self.peer_status = None
        peer_active = reply is not None and reply.get('role') in (HA_ROLE_ACTIVE, HA_ROLE_DRAINING)

        if self.role == HA_ROLE_PASSIVE:
            await self._tick_passive(reply, peer_active, now)
        elif self.role == HA_ROLE_ACTIVE:
            await self._tick_active(reply, peer_active)
        elif self.role == HA_ROLE_DRAINING:
            await self._tick_draining(reply, peer_active, now)

    async def _tick_passive(self, reply: dict | None, peer_active: bool, now: float):
        if peer_active:
            try:
                await self._pull(reply)
            except SyncError as e:
                await self.notifier.error(f"⚠️ {self.node} sync error: {e}")
            if self.handoff_pending and reply.get('drained'):
                await self._final_pull_and_activate('handoff')
            elif not self.handoff_pending and self._should_request_handoff(reply, now):
                manual = bool(reply.get('handback_requested')) or self._manual_takeover
                if await self.peer.request_handoff(manual=manual):
                    self.handoff_pending = True
                    self._handoff_manual = manual
                    await self.notifier.transition(f"⏳ {self.node} requested handoff from {self.peer_name}")
            return

        # No active peer in sight
        if self._manual_takeover:
            await self._activate('manual takeover')
            return
        if self.last_active_seen is None:
            wait = self.config.heartbeat_interval_sec if self.config.is_primary \
                else self.config.lease_ttl_sec + self.config.heartbeat_interval_sec
            lost = now - self.started_at >= wait
            reason = 'cold start'
        else:
            lost = now - self.last_active_seen >= self.config.lease_ttl_sec
            reason = 'lease lost'
        if not lost:
            return
        if self.is_ready():
            await self._activate(reason)
        else:
            await self.notifier.error(
                f"⚠️ {self.node} cannot take over ({reason}): {self.readiness_reason()}")

    async def _tick_active(self, reply: dict | None, peer_active: bool):
        if peer_active and reply.get('role') == HA_ROLE_ACTIVE:
            if self.config.is_primary:
                logger.warning("both nodes ACTIVE; the standby should yield")
                await self.notifier.error(f"⚠️ both nodes are ACTIVE — {self.peer_name} should yield")
            else:
                await self._step_down('peer active')

    async def _tick_draining(self, reply: dict | None, peer_active: bool, now: float):
        if not self.drained:
            return
        if peer_active and reply.get('role') == HA_ROLE_ACTIVE:
            await self._become_passive_after_handoff()
        elif now - self.drain_finished_at >= self.config.lease_ttl_sec:
            await self._activate('peer never took over')

    def _should_request_handoff(self, reply: dict, now: float) -> bool:
        if not self.is_ready():
            return False
        if reply.get('handback_requested') or self._manual_takeover:
            return True
        if not self.config.is_primary or reply.get('manual_hold'):
            return False
        return self.healthy_since is not None and now - self.healthy_since >= self.config.failback_stable_sec

    async def _pull(self, reply: dict):
        """Normal passive pull — or, once per handoff, the returning-node reconcile."""
        handoff_rev = reply.get('handoff_rev')
        if reply.get('role') == HA_ROLE_ACTIVE and handoff_rev is not None \
                and self._reconciled_for != (self.peer_name, handoff_rev):
            async def pull(since: int) -> set:
                return (await self.peer.pull_changes(since=since))['touched']
            result = await self.state.reconcile_after_return(int(handoff_rev), pull)
            self._reconciled_for = (self.peer_name, handoff_rev)
            if result['restamped']:
                logger.info("reconciled after return: %s", result)
            return
        await self.peer.pull_changes()

    # --- transitions --------------------------------------------------------

    async def _activate(self, reason: str):
        manual = self._manual_takeover or self._handoff_manual
        self._manual_takeover = False
        self._handoff_manual = False
        # Keep an older persisted handoff point (a restart while ACTIVE must
        # not move it, or the returning peer's unseen rows would never be re-stamped)
        self.handoff_rev = self.state.rev if self.handoff_rev is None else min(self.handoff_rev, self.state.rev)
        await self._persist(META_HANDOFF_REV, self.handoff_rev)
        if manual and not self.config.is_primary and not self.manual_hold:
            self.manual_hold = True
            await self._persist(META_MANUAL_HOLD, True)
        self.role = HA_ROLE_ACTIVE
        self.drained = False
        self.handoff_pending = False
        self.handback_requested = False
        self._stepping_down = False
        since = None
        if self.peer_active_wall_at is not None:
            since = self.peer_active_wall_at - timedelta(seconds=self.config.heartbeat_interval_sec)
        await self.runner.start(backlog_since=since)
        await self.notifier.transition(f"🟢 {self.node} is now ACTIVE ({reason})")

    async def _final_pull_and_activate(self, reason: str):
        try:
            await self.peer.pull_changes()
        except SyncError as e:
            # Never activate on stale data: stay PASSIVE, keep handoff_pending, retry next tick
            await self.notifier.error(f"⚠️ {self.node} final pull before takeover failed: {e}")
            return
        await self._activate(reason)

    async def _start_drain(self):
        self.role = HA_ROLE_DRAINING
        self.drained = False
        self.handback_requested = False
        await self.runner.stop_polling()
        self.drain_task = asyncio.create_task(self._run_drain(), name='ha_drain')

    async def _run_drain(self):
        try:
            await self.runner.drain()
        except Exception:  # noqa: BLE001
            logger.error("drain failed; handing over anyway", exc_info=True)
        self.drained = True
        self.drain_finished_at = self.clock()
        await self.notifier.transition(f"🟡 {self.node} drained, waiting for {self.peer_name} to take over")

    async def _become_passive_after_handoff(self):
        self.role = HA_ROLE_PASSIVE
        self.drained = False
        self.handoff_pending = False
        self.handoff_rev = None
        await self._persist(META_HANDOFF_REV, None)
        await self.peer.on_handed_back()
        await self.notifier.transition(f"⚪ {self.node} handed over to {self.peer_name}, now PASSIVE")

    async def _step_down(self, reason: str):
        try:
            await self.runner.stop_all()
        finally:
            self.role = HA_ROLE_PASSIVE
            self.drained = False
            self.handoff_pending = False
            self.handback_requested = False
            self.handoff_rev = None
            self._stepping_down = False
        await self._persist(META_HANDOFF_REV, None)
        await self.peer.on_handed_back()
        await self.notifier.transition(f"🔴 {self.node} stepped down ({reason})")

    # --- helpers ------------------------------------------------------------

    def _peer_active(self) -> bool:
        return bool(self.peer_status and self.peer_status.get('role') in (HA_ROLE_ACTIVE, HA_ROLE_DRAINING))

    def _peer_seen_at(self) -> str | None:
        if self.peer is None:
            return None
        return self.peer.sync_status().get('last_heartbeat_at')

    async def _persist(self, key: str, value):
        await set_meta(self.state.db, key, value)
        await self.state.db.commit()
