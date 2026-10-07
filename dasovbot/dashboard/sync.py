"""Peer-facing HA endpoints (`/sync/*`, bearer SYNC_SECRET) and the public `/health`."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import tempfile
from datetime import datetime, timedelta

from aiohttp import web

from dasovbot.constants import DATETIME_FORMAT, HA_ROLE_ACTIVE, SYNC_PAGE_SIZE
from dasovbot.dashboard.views import get_ha, get_state
from dasovbot.database import read_changes, write_snapshot

logger = logging.getLogger(__name__)

HEALTH_KEYS = ('role', 'node', 'lease_holder', 'last_sync_rev', 'last_sync_at', 'ready', 'peer', 'peer_url',
               'node_role', 'enabled', 'rev', 'manual_hold', 'handback_requested', 'drained')
HEARTBEAT_KEYS = ('node', 'role', 'lease_until', 'rev', 'drained', 'manual_hold', 'handback_requested',
                  'handoff_rev')
SNAPSHOT_CHUNK = 1 << 20


def _node_name(state) -> str:
    config = getattr(state, 'config', None)
    return (getattr(config, 'node_name', '') or socket.gethostname()) if config else socket.gethostname()


async def health(request: web.Request) -> web.Response:
    """Unauthenticated: who is active here, and is this node fit to take over."""
    ha = get_ha(request)
    if ha is None:
        # No controller registered (standalone/pre-HA process): behave as today
        state = get_state(request)
        node = _node_name(state)
        return web.json_response({
            'role': HA_ROLE_ACTIVE, 'node': node, 'lease_holder': node, 'last_sync_rev': None,
            'last_sync_at': None, 'ready': True, 'peer': None, 'peer_url': '', 'node_role': 'primary',
            'enabled': False, 'rev': getattr(state, 'rev', 0), 'manual_hold': False,
            'handback_requested': False, 'drained': False,
        })
    status = ha.status()
    return web.json_response({key: status.get(key) for key in HEALTH_KEYS})


async def sync_heartbeat(request: web.Request) -> web.Response:
    ha = get_ha(request)
    if ha is None:
        return web.json_response({'error': 'sync disabled'}, status=503)
    status = ha.status()
    reply = {key: status.get(key) for key in HEARTBEAT_KEYS}
    # Informational: the puller judges the lease on its own clock
    reply['lease_until'] = (datetime.now() + timedelta(seconds=ha.config.lease_ttl_sec)).strftime(DATETIME_FORMAT)
    return web.json_response(reply)


async def sync_changes(request: web.Request) -> web.Response:
    """Feed page; served in every role (a PASSIVE node's survivors flow back through it)."""
    try:
        since = int(request.query.get('since', 0))
        limit = int(request.query.get('limit', SYNC_PAGE_SIZE))
    except ValueError:
        return web.json_response({'error': 'since and limit must be integers'}, status=400)
    if since < 0 or limit < 1:
        return web.json_response({'error': 'since must be >= 0 and limit >= 1'}, status=400)
    page = await read_changes(get_state(request).db, since, min(limit, SYNC_PAGE_SIZE))
    return web.json_response(page)


async def sync_snapshot(request: web.Request) -> web.StreamResponse:
    """Stream a consistent SQLite copy of the live database (backup API into a temp file)."""
    state = get_state(request)
    # System temp dir, never under /data: Syncthing would ship the file
    fd, path = tempfile.mkstemp(prefix='dasovbot-snapshot-', suffix='.db')
    os.close(fd)
    try:
        await write_snapshot(state.db, path)
        size = os.path.getsize(path)
        response = web.StreamResponse(headers={
            'Content-Type': 'application/x-sqlite3',
            'Content-Length': str(size),
            'X-Dasovbot-Rev': str(getattr(state, 'rev', 0)),
        })
        await response.prepare(request)
        loop = asyncio.get_running_loop()
        with open(path, 'rb') as f:
            while True:
                chunk = await loop.run_in_executor(None, f.read, SNAPSHOT_CHUNK)
                if not chunk:
                    break
                await response.write(chunk)
        await response.write_eof()
        return response
    finally:
        for leftover in (path, path + '-wal', path + '-shm', path + '-journal'):
            try:
                os.remove(leftover)
            except FileNotFoundError:
                pass


async def sync_handoff(request: web.Request) -> web.Response:
    """The passive peer asks this (ACTIVE) node to drain and hand over the lease."""
    from dasovbot.services.ha import HaError
    ha = get_ha(request)
    if ha is None:
        return web.json_response({'error': 'sync disabled'}, status=503)
    manual = False
    if request.can_read_body:
        try:
            body = await request.json()
            manual = bool(body.get('manual', False)) if isinstance(body, dict) else False
        except ValueError:
            return web.json_response({'error': 'body must be JSON'}, status=400)
    was_draining = ha.role == 'draining'
    try:
        result = await ha.handoff_requested_by_peer(manual=manual)
    except HaError as e:
        return web.json_response({'error': str(e)}, status=409)
    return web.json_response(result, status=200 if was_draining else 202)
