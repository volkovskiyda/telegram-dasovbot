"""Sync client: the passive node's view of the peer, and the developer notifier.

`SyncClient` implements the controller's `PeerClient` port over the peer's
dashboard (`/sync/*`, bearer SYNC_SECRET): heartbeat, incremental change-feed
pulls, the hourly snapshot reconcile and the handoff request. Readiness
timestamps are persisted in `sync_meta` so a restarted standby can judge its
own data before taking over.
"""
from __future__ import annotations

import asyncio
import logging
import os
from tempfile import mkstemp
import time
from datetime import datetime
from urllib.parse import urlsplit

import aiohttp

from dasovbot.config import Config
from dasovbot.constants import DATETIME_FORMAT, SNAPSHOT_INTERVAL_SEC, SYNC_ERROR_NOTIFY_INTERVAL_SEC, SYNC_PAGE_SIZE
from dasovbot.database import get_meta, set_meta, last_applied_key, reconcile_from_snapshot
from dasovbot.services.ha import SyncError  # noqa: F401 — re-exported: the port's contract lives in ha.py
from dasovbot.state import BotState

logger = logging.getLogger(__name__)

META_LAST_SYNC_AT = 'sync:last_sync_at'
META_LAST_HEARTBEAT_AT = 'sync:last_heartbeat_at'
META_LAST_SYNC_REV = 'sync:last_sync_rev'
META_LAST_SNAPSHOT_AT = 'sync:last_snapshot_at'

MAX_PAGES_PER_TICK = 200          # bounds one passive tick; the next tick continues
MAX_PAGES_PER_RECONCILE = 20_000  # an explicit `since` pulls to the end; this only stops a runaway feed
SNAPSHOT_CHUNK = 1 << 20


def _now() -> str:
    return datetime.now().strftime(DATETIME_FORMAT)


class SyncClient:
    def __init__(self, config: Config, state: BotState, notifier=None,
                 session_factory=aiohttp.ClientSession, clock=time.monotonic):
        self.config = config
        self.state = state
        self.notifier = notifier
        self._session_factory = session_factory
        self._session: aiohttp.ClientSession | None = None
        self.clock = clock
        self._peer_node: str | None = None
        self.last_sync_at: str | None = None
        self.last_heartbeat_at: str | None = None
        self.last_sync_rev: int | None = None
        self.last_snapshot_at: str | None = None
        self._last_snapshot_mono: float | None = None

    # --- lifecycle ----------------------------------------------------------

    async def load(self):
        """Restore the readiness bookkeeping persisted by an earlier run."""
        db = self.state.db
        self.last_sync_at = await get_meta(db, META_LAST_SYNC_AT)
        self.last_heartbeat_at = await get_meta(db, META_LAST_HEARTBEAT_AT)
        self.last_sync_rev = await get_meta(db, META_LAST_SYNC_REV)
        self.last_snapshot_at = await get_meta(db, META_LAST_SNAPSHOT_AT)

    async def aclose(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _session_or_open(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._session_factory(
                headers={'Authorization': f'Bearer {self.config.sync_secret}'})
        return self._session

    def _url(self, path: str) -> str:
        return f'{self.config.peer_url}{path}'

    @property
    def peer_name(self) -> str:
        if self._peer_node:
            return self._peer_node
        return urlsplit(self.config.peer_url).hostname or self.config.peer_url or 'peer'

    def sync_status(self) -> dict:
        return {
            'last_sync_at': self.last_sync_at,
            'last_heartbeat_at': self.last_heartbeat_at,
            'last_sync_rev': self.last_sync_rev,
            'last_snapshot_at': self.last_snapshot_at,
            'peer': self.peer_name,
        }

    async def _persist(self, **values):
        db = self.state.db
        for key, value in values.items():
            await set_meta(db, key, value)
        await db.commit()

    # --- PeerClient port ----------------------------------------------------

    async def heartbeat(self) -> dict | None:
        timeout = aiohttp.ClientTimeout(total=self.config.heartbeat_interval_sec)
        try:
            async with self._session_or_open().get(self._url('/sync/heartbeat'), timeout=timeout) as resp:
                if resp.status != 200:
                    logger.warning("heartbeat to %s: HTTP %d", self.peer_name, resp.status)
                    return None
                reply = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.warning("heartbeat to %s failed: %s", self.peer_name, type(e).__name__)
            return None
        if not isinstance(reply, dict) or 'role' not in reply:
            logger.warning("heartbeat to %s: malformed reply", self.peer_name)
            return None
        if reply.get('node'):
            self._peer_node = str(reply['node'])
        self.last_heartbeat_at = _now()
        await self._persist(**{META_LAST_HEARTBEAT_AT: self.last_heartbeat_at})
        return reply

    async def request_handoff(self, manual: bool) -> bool:
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with self._session_or_open().post(
                    self._url('/sync/handoff'), json={'manual': manual, 'node': self.config.node_name},
                    timeout=timeout) as resp:
                if resp.status in (200, 202):
                    return True
                detail = ''
                if resp.status == 409:
                    try:
                        detail = (await resp.json()).get('error', '')
                    except (ValueError, aiohttp.ClientError):
                        detail = ''
                logger.warning("handoff request to %s refused: HTTP %d %s", self.peer_name, resp.status, detail)
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("handoff request to %s failed: %s", self.peer_name, type(e).__name__)
            return False

    async def pull_changes(self, since: int | None = None) -> dict:
        """Pull and apply the peer's feed from the stored cursor (or an explicit `since`).

        A fresh node (empty tables, cursor 0) bootstraps with a snapshot first,
        then continues incrementally. Without `since` one call is capped at
        MAX_PAGES_PER_TICK pages; with it the pull runs to the end (reconcile).
        """
        explicit = since is not None
        cursor_key = last_applied_key(self.peer_name)
        if await self._tables_empty():
            # Nothing local to protect: always bootstrap from a snapshot, even
            # when the controller asked for a reconcile pull from a given rev
            # (a fresh node has no survivors, but a partial feed would leave
            # it "ready" with a near-empty database)
            await self.pull_snapshot()
            cursor = int(await get_meta(self.state.db, cursor_key, 0) or 0)
            since = cursor if since is None else max(int(since), cursor)
        elif not explicit:
            since = int(await get_meta(self.state.db, cursor_key, 0) or 0)
        cap = MAX_PAGES_PER_RECONCILE if explicit else MAX_PAGES_PER_TICK
        applied, touched, pages = 0, set(), 0
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            while pages < cap:
                url = self._url(f'/sync/changes?since={int(since)}&limit={SYNC_PAGE_SIZE}')
                async with self._session_or_open().get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        raise SyncError(f'changes: HTTP {resp.status}')
                    page = await resp.json()
                result = await self.state.apply_remote_page(page, self.peer_name)
                applied += result['rows'] + result['tombstones'] + result['requests']
                touched |= result['touched']
                pages += 1
                since = page['until']
                if not page['has_more']:
                    break
            else:
                if explicit:
                    raise SyncError(f'changes: feed did not end after {cap} pages')
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as e:
            raise SyncError(f'changes: {type(e).__name__}: {e}') from e
        self.last_sync_at = _now()
        self.last_sync_rev = int(since)
        await self._persist(**{META_LAST_SYNC_AT: self.last_sync_at, META_LAST_SYNC_REV: self.last_sync_rev})
        if applied:
            logger.info("synced %d changes from %s (rev %s, %d pages)", applied, self.peer_name, since, pages)
        return {'applied': applied, 'touched': touched}

    async def pull_snapshot(self):
        """Download the peer's snapshot into a temp file and reconcile the local database from it."""
        fd, path = mkstemp(prefix='dasovbot-snapshot-', suffix='.db')
        os.close(fd)
        timeout = aiohttp.ClientTimeout(total=900)
        try:
            try:
                async with self._session_or_open().get(self._url('/sync/snapshot'), timeout=timeout) as resp:
                    if resp.status != 200:
                        raise SyncError(f'snapshot: HTTP {resp.status}')
                    header_rev = resp.headers.get('X-Dasovbot-Rev')
                    loop = asyncio.get_running_loop()
                    with open(path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(SNAPSHOT_CHUNK):
                            await loop.run_in_executor(None, f.write, chunk)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                raise SyncError(f'snapshot: {type(e).__name__}: {e}') from e
            try:
                result = await reconcile_from_snapshot(self.state.db, path, self.peer_name)
            except Exception as e:  # noqa: BLE001 — a corrupt download must not kill the loop
                raise SyncError(f'snapshot reconcile: {type(e).__name__}: {e}') from e
            await self.state.reload_from_db()
        finally:
            for leftover in (path, path + '-wal', path + '-shm', path + '-journal'):
                try:
                    os.remove(leftover)
                except FileNotFoundError:
                    pass
        self._last_snapshot_mono = self.clock()
        self.last_snapshot_at = self.last_sync_at = _now()
        self.last_sync_rev = int(header_rev) if header_rev and header_rev.isdigit() else int(result['snapshot_rev'])
        await self._persist(**{META_LAST_SNAPSHOT_AT: self.last_snapshot_at, META_LAST_SYNC_AT: self.last_sync_at,
                               META_LAST_SYNC_REV: self.last_sync_rev})
        logger.info("snapshot from %s applied: %s", self.peer_name, result['changed'])

    async def maybe_snapshot(self):
        """Hourly self-healing floor; the first call only arms the timer."""
        now = self.clock()
        if self._last_snapshot_mono is None:
            self._last_snapshot_mono = now
            return
        if now - self._last_snapshot_mono >= SNAPSHOT_INTERVAL_SEC:
            await self.pull_snapshot()

    async def on_handed_back(self):
        """This node just stopped being ACTIVE: its data is current as of now.

        Moves the feed cursor past everything it wrote (re-stamped survivors
        sort above it) and marks the data fresh, so a takeover right after a
        step-down is not refused for a stale `last_sync_at`.
        """
        key = last_applied_key(self.peer_name)
        current = int(await get_meta(self.state.db, key, 0) or 0)
        cursor = max(current, self.state.rev)
        self.last_sync_at = _now()
        self.last_sync_rev = cursor
        await self._persist(**{key: cursor, META_LAST_SYNC_AT: self.last_sync_at, META_LAST_SYNC_REV: cursor})

    # --- helpers ------------------------------------------------------------

    async def _tables_empty(self) -> bool:
        return not (self.state.videos or self.state.users or self.state.subscriptions or self.state.intents)


class DeveloperNotifier:
    """Transition messages always; error messages at most once per SYNC_ERROR_NOTIFY_INTERVAL_SEC."""

    def __init__(self, config: Config, clock=time.monotonic):
        self.config = config
        self.clock = clock
        self.bot = None
        self._last_error_sent: float | None = None

    def attach_bot(self, bot):
        self.bot = bot

    async def transition(self, text: str):
        logger.info("HA: %s", text)
        await self._send(text)

    async def error(self, text: str):
        logger.error("HA: %s", text)
        now = self.clock()
        if self._last_error_sent is not None and now - self._last_error_sent < SYNC_ERROR_NOTIFY_INTERVAL_SEC:
            return
        self._last_error_sent = now
        await self._send(text)

    async def _send(self, text: str):
        if self.bot is None or not self.config.developer_chat_id:
            return
        try:
            await self.bot.send_message(chat_id=self.config.developer_chat_id, text=text)
        except Exception:  # noqa: BLE001 — notifications never break the controller
            logger.warning("developer notification failed", exc_info=True)
