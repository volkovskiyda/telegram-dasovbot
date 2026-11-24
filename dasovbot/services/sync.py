"""Sync client: the passive node's view of the peer, and the developer notifier.

`SyncClient` implements the controller's `PeerClient` port over the peer's
dashboard (`/sync/*`, bearer SYNC_SECRET): heartbeat, incremental change-feed
pulls, the hourly snapshot reconcile and the handoff request. Readiness
timestamps are persisted in `sync_meta` so a restarted standby can judge its
own data before taking over.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
from tempfile import mkstemp
import time
from datetime import datetime
from urllib.parse import urlsplit

import aiohttp

from dasovbot.config import Config
from dasovbot.constants import (
    DATETIME_FORMAT, ERROR_EPISODE_IDLE_SEC, ERROR_REMIND_INTERVAL_SEC, SNAPSHOT_INTERVAL_SEC, SNAPSHOT_RETRY_MIN_SEC,
    SYNC_PAGE_SIZE, TRANSITION_COLLAPSE_SEC,
)
from dasovbot.database import get_meta, set_meta, last_applied_key, reconcile_from_snapshot, validate_snapshot
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
SOCK_READ_TIMEOUT_SEC = 30        # longest silence tolerated mid-response from the peer


def _now() -> str:
    return datetime.now().strftime(DATETIME_FORMAT)


def fmt_span(seconds: float) -> str:
    """`45 s`, `12 min`, `7 h 24 min`."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f'{seconds} s'
    hours, minutes = divmod(seconds // 60, 60)
    if not hours:
        return f'{minutes} min'
    return f'{hours} h {minutes} min' if minutes else f'{hours} h'


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
        self.snapshot_failures = 0                    # consecutive; cleared by an applied snapshot
        self._snapshot_retry_at: float | None = None  # monotonic: no new snapshot download before this

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
        except (TimeoutError, aiohttp.ClientError, ValueError) as e:
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
        except (TimeoutError, aiohttp.ClientError) as e:
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
            wait = self.snapshot_retry_in()
            if wait is not None:
                raise SyncError(f'bootstrap snapshot failed {self.snapshot_failures}× in a row; '
                                f'next try in {fmt_span(wait)}')
            await self.pull_snapshot()
            cursor = int(await get_meta(self.state.db, cursor_key, 0) or 0)
            since = cursor if since is None else max(int(since), cursor)
        elif not explicit:
            since = int(await get_meta(self.state.db, cursor_key, 0) or 0)
        cap = MAX_PAGES_PER_RECONCILE if explicit else MAX_PAGES_PER_TICK
        applied, touched, pages = 0, set(), 0
        # sock_read: a peer host that loses power mid-response sends no RST,
        # and the controller's tick (hence the lease check) waits on this
        timeout = aiohttp.ClientTimeout(total=60, sock_read=SOCK_READ_TIMEOUT_SEC)
        try:
            while pages < cap:
                url = self._url(f'/sync/changes?since={int(since)}&limit={SYNC_PAGE_SIZE}')
                async with self._session_or_open().get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        raise SyncError(f'changes: HTTP {resp.status}')
                    page = await resp.json()
                # A reconcile pull is the peer's view of everything above the
                # handoff point, and the peer wins there: its tombstones apply
                # even to a local row stamped higher by this node's own counter
                # (the two counters are not comparable above that point)
                result = await self.state.apply_remote_page(page, self.peer_name, force_tombstones=explicit)
                applied += result['rows'] + result['tombstones'] + result['requests']
                touched |= result['touched']
                pages += 1
                since = page['until']
                if not page['has_more']:
                    break
            else:
                if explicit:
                    raise SyncError(f'changes: feed did not end after {cap} pages')
        except (TimeoutError, aiohttp.ClientError, ValueError, KeyError, TypeError) as e:
            raise SyncError(f'changes: {type(e).__name__}: {e}') from e
        self.last_sync_at = _now()
        self.last_sync_rev = int(since)
        await self._persist(**{META_LAST_SYNC_AT: self.last_sync_at, META_LAST_SYNC_REV: self.last_sync_rev})
        if applied:
            logger.info("synced %d changes from %s (rev %s, %d pages)", applied, self.peer_name, since, pages)
        return {'applied': applied, 'touched': touched}

    def snapshot_retry_in(self) -> float | None:
        """Seconds until a failed snapshot may be downloaded again, None when nothing holds it back."""
        if self._snapshot_retry_at is None:
            return None
        remaining = self._snapshot_retry_at - self.clock()
        return remaining if remaining > 0 else None

    async def pull_snapshot(self):
        """Download the peer's snapshot into a temp file and reconcile the local database from it.

        A failure arms the retry backoff (SNAPSHOT_RETRY_MIN_SEC doubling per
        consecutive failure, capped at SNAPSHOT_INTERVAL_SEC) that
        maybe_snapshot and the bootstrap honour; the SyncError says when the
        next try is due. An applied snapshot clears it.
        """
        try:
            await self._download_and_apply_snapshot()
        except SyncError as e:
            self.snapshot_failures += 1
            delay = min(SNAPSHOT_RETRY_MIN_SEC * 2 ** (self.snapshot_failures - 1), SNAPSHOT_INTERVAL_SEC)
            self._snapshot_retry_at = self.clock() + delay
            logger.warning("snapshot from %s failed (%d in a row), next try in %s: %s",
                           self.peer_name, self.snapshot_failures, fmt_span(delay), e)
            raise SyncError(f'{e}; next try in {fmt_span(delay)}') from e
        self.snapshot_failures = 0
        self._snapshot_retry_at = None

    async def _download_and_apply_snapshot(self):
        fd, path = mkstemp(prefix='dasovbot-snapshot-', suffix='.db')
        os.close(fd)
        # Without sock_read a stalled stream (peer host lost power, no RST)
        # held the passive tick for the whole 15 min with nobody polling
        timeout = aiohttp.ClientTimeout(total=900, sock_read=SOCK_READ_TIMEOUT_SEC)
        try:
            try:
                async with self._session_or_open().get(self._url('/sync/snapshot'), timeout=timeout) as resp:
                    if resp.status != 200:
                        raise SyncError(f'snapshot: HTTP {resp.status}')
                    header_rev = resp.headers.get('X-Dasovbot-Rev')
                    # Absent on a peer that predates the checksum: accepted, the row check still runs
                    expected_sha = resp.headers.get('X-Dasovbot-Sha256')
                    digest = hashlib.sha256()
                    loop = asyncio.get_running_loop()
                    with open(path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(SNAPSHOT_CHUNK):
                            digest.update(chunk)
                            await loop.run_in_executor(None, f.write, chunk)
            except (TimeoutError, aiohttp.ClientError, OSError) as e:
                raise SyncError(f'snapshot: {type(e).__name__}: {e}') from e
            if expected_sha and digest.hexdigest() != expected_sha.lower():
                raise SyncError(f'snapshot rejected: checksum mismatch ({digest.hexdigest()[:12]} != {expected_sha[:12]})')
            try:
                await loop.run_in_executor(None, validate_snapshot, path)
            except (ValueError, sqlite3.DatabaseError) as e:
                raise SyncError(f'snapshot rejected: {e}') from e
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

    async def maybe_snapshot(self) -> bool:
        """Hourly self-healing floor; the first call only arms the timer. After a failure the
        next try waits for the backoff instead of the hour. True when a snapshot was applied."""
        now = self.clock()
        if self._snapshot_retry_at is not None:
            if now < self._snapshot_retry_at:
                return False
        elif self._last_snapshot_mono is None:
            self._last_snapshot_mono = now
            return False
        elif now - self._last_snapshot_mono < SNAPSHOT_INTERVAL_SEC:
            return False
        await self.pull_snapshot()
        return True

    async def on_handed_back(self):
        """This node just stopped being ACTIVE: its data is current as of now.

        Marks the data fresh so a takeover right after a step-down is not
        refused for a stale `last_sync_at`. The feed cursor is deliberately
        left where the last passive pull put it: the peer re-stamped its
        survivors while this node was still ACTIVE and writing, so those revs
        sit *below* this node's counter. Jumping the cursor to the counter
        skipped them and only the hourly snapshot healed the divergence. The
        rows this node wrote while active come back once through the feed and
        apply as no-ops.
        """
        key = last_applied_key(self.peer_name)
        cursor = int(await get_meta(self.state.db, key, 0) or 0)
        self.last_sync_at = _now()
        self.last_sync_rev = cursor
        await self._persist(**{META_LAST_SYNC_AT: self.last_sync_at, META_LAST_SYNC_REV: cursor})

    # --- helpers ------------------------------------------------------------

    async def _tables_empty(self) -> bool:
        return not (self.state.videos or self.state.users or self.state.subscriptions or self.state.intents)


class _Episode:
    """One run of failures of the same kind, from the first alert to the recovery."""

    __slots__ = ('started', 'last_failure', 'last_sent', 'failures', 'ok_since')

    def __init__(self, now: float):
        self.started = self.last_failure = self.last_sent = now
        self.failures = 1
        self.ok_since: float | None = None


class DeveloperNotifier:
    """Transition messages always (quiet ones go to the history only); errors in episodes.

    An error opens an episode for its `key` (default: one shared episode) and
    is sent at once. Repeats are counted; while they continue, one silent
    "still failing" reminder goes out per ERROR_REMIND_INTERVAL_SEC.
    `resolved(key, text)` closes the episode with one silent recovery message
    that says how long it failed. An episode that nobody resolves ends after
    ERROR_EPISODE_IDLE_SEC without a repeat, so the next failure alerts again.

    With a `state`, every transition (collapsed repeats included) and every
    error, reminder and recovery notice that is actually sent is also appended
    to `state.ha_events` for the system page.
    """

    def __init__(self, config: Config, clock=time.monotonic, state: BotState | None = None):
        self.config = config
        self.clock = clock
        self.state = state
        self.bot = None
        self._episodes: dict[str, _Episode] = {}
        self._recent: dict[str, tuple[float, int]] = {}   # text -> (first sent at, repeats collapsed since)

    def attach_bot(self, bot):
        self.bot = bot

    async def transition(self, text: str, quiet: bool = False):
        """Sent at once, except an exact repeat within TRANSITION_COLLAPSE_SEC: that is counted and the
        count is appended to the next copy sent (a flapping node yields one message per window).
        A `quiet` step (the intermediate ones of a planned handoff) is recorded and logged only."""
        now = self.clock()
        await self._record(text, 'transition')
        if quiet:
            logger.info("HA: %s", text)
            return
        first_at, collapsed = self._recent.get(text, (None, 0))
        if first_at is not None and now - first_at < TRANSITION_COLLAPSE_SEC:
            self._recent[text] = (first_at, collapsed + 1)
            logger.info("HA: %s (repeat, collapsed)", text)
            return
        self._recent = {k: v for k, v in self._recent.items() if now - v[0] < TRANSITION_COLLAPSE_SEC}
        self._recent[text] = (now, 0)
        if collapsed:
            text = f"{text} (+{collapsed} collapsed in the last {int(round((now - first_at) / 60))} min)"
        logger.info("HA: %s", text)
        await self._send(text)

    async def notify(self, text: str):
        """Always sent, never collapsed: the admin API's audit lines (two bans in a row are two lines)."""
        logger.info("API: %s", text)
        await self._send(text)

    async def error(self, text: str, key: str | None = None):
        logger.error("HA: %s", text)
        key = key or 'error'
        now = self.clock()
        episode = self._episodes.get(key)
        if episode is not None and now - episode.last_failure >= ERROR_EPISODE_IDLE_SEC:
            episode = None                      # went quiet long ago without a recovery: a new episode
        if episode is None:
            self._episodes[key] = _Episode(now)
            await self._record(text, 'error')
            await self._send(text)
            return
        episode.failures += 1
        episode.last_failure = now
        episode.ok_since = None
        if now - episode.last_sent < ERROR_REMIND_INTERVAL_SEC:
            return
        episode.last_sent = now
        text = f"{text}\n⏳ still failing after {fmt_span(now - episode.started)} ({episode.failures} tries)"
        await self._record(text, 'error')
        await self._send(text, silent=True)

    async def resolved(self, key: str, text: str | None = None, confirm_sec: float = 0):
        episode = self._episodes.get(key)
        if episode is None:
            return
        now = self.clock()
        if confirm_sec:
            if episode.ok_since is None:
                episode.ok_since = now
            if now - episode.ok_since < confirm_sec:
                return
        del self._episodes[key]
        if text is None:
            logger.info("HA: %s episode over (%d failures)", key, episode.failures)
            return
        tries = f"{episode.failures} failed tr{'y' if episode.failures == 1 else 'ies'}"
        text = f"{text} (after {fmt_span(now - episode.started)}, {tries})"
        logger.info("HA: %s", text)
        await self._record(text, 'recovered')
        await self._send(text, silent=True)

    async def _record(self, text: str, kind: str):
        if self.state is None:
            return
        try:
            await self.state.record_ha_event(text, kind)
        except Exception:  # noqa: BLE001 — history is best effort
            logger.warning("recording HA event failed", exc_info=True)

    async def _send(self, text: str, silent: bool = False):
        if self.bot is None or not self.config.developer_chat_id:
            return
        kwargs = {'disable_notification': True} if silent else {}
        try:
            await self.bot.send_message(chat_id=self.config.developer_chat_id, text=text, **kwargs)
        except Exception:  # noqa: BLE001 — notifications never break the controller
            logger.warning("developer notification failed", exc_info=True)
