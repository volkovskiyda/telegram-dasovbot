from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp_jinja2
import jinja2
from aiohttp import web

from dasovbot.dashboard.api import api_video, api_videos
from dasovbot.dashboard.admin_api import (
    api_alerts, api_intents, api_playlists, api_status, api_subscriptions, api_users,
    api_ban, api_unban, api_subscribe, api_unsubscribe, api_send, api_populate,
)
from dasovbot.dashboard.auth import auth_middleware, login_page, login_post, logout, get_password, get_api_token, get_admin_token
from dasovbot.dashboard.sync import health, sync_heartbeat, sync_changes, sync_snapshot, sync_handoff
from dasovbot.dashboard.views import index, videos, ignored, retry_ignored, remove_ignored, remove_intent, force_populate, subscriptions, remove_subscription, users, ban_user, unban_user, system, takeover, handback, health_alerts_processor, ha_processor, role_guard_middleware, STATE_KEY, HA_KEY, VPN_KEY
from dasovbot.services.background import run_forever
from dasovbot.services.vpn import VpnMonitor

if TYPE_CHECKING:
    from dasovbot.state import BotState

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'


def format_duration(seconds: int) -> str:
    if not seconds:
        return '0:00'
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    if m:
        return f'{m}:{s:02d}'
    return f'0:{s:02d}'


def safe_url(url: str | None) -> str:
    if url and url.startswith(('http://', 'https://')):
        return url
    return '#'


VPN_TASK_KEY = web.AppKey('vpn_task', asyncio.Task)


def create_app(state: BotState, ha=None, vpn: VpnMonitor | None = None) -> web.Application:
    """`ha` is the RoleController; without it the node reports itself standalone/active.
    `vpn` is the VpnMonitor whose status `/api/status` reports (None = `vpn: null`)."""
    # Auth first, then the HA write guard (a passive node answers POSTs with 409)
    app = web.Application(middlewares=[auth_middleware, role_guard_middleware])
    app[STATE_KEY] = state
    if ha is not None:
        app[HA_KEY] = ha
    if vpn is not None:
        app[VPN_KEY] = vpn

    env = aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        context_processors=[health_alerts_processor, ha_processor, aiohttp_jinja2.request_processor],
    )
    env.filters['duration'] = format_duration
    env.filters['safe_url'] = safe_url

    app.router.add_static('/static', STATIC_DIR, name='static')
    app.router.add_get('/login', login_page)
    app.router.add_post('/login', login_post)
    app.router.add_get('/logout', logout)
    app.router.add_get('/', index)
    app.router.add_get('/videos', videos)
    app.router.add_get('/ignored', ignored)
    app.router.add_post('/ignored/retry', retry_ignored)
    app.router.add_post('/ignored/remove', remove_ignored)
    app.router.add_post('/intent/remove', remove_intent)
    app.router.add_get('/subscriptions', subscriptions)
    app.router.add_post('/subscriptions/remove', remove_subscription)
    app.router.add_get('/users', users)
    app.router.add_post('/users/ban', ban_user)
    app.router.add_post('/users/unban', unban_user)
    app.router.add_post('/system/populate', force_populate)
    app.router.add_get('/system', system)
    app.router.add_post('/system/takeover', takeover)
    app.router.add_post('/system/handback', handback)
    # Read-only JSON API (API_TOKEN or ADMIN_TOKEN)
    app.router.add_get('/api/videos', api_videos)
    app.router.add_get('/api/videos/{video_id}', api_video)
    # Admin API (ADMIN_TOKEN)
    app.router.add_get('/api/status', api_status)
    app.router.add_get('/api/users', api_users)
    app.router.add_get('/api/subscriptions', api_subscriptions)
    app.router.add_get('/api/intents', api_intents)
    app.router.add_get('/api/alerts', api_alerts)
    app.router.add_get('/api/playlists', api_playlists)
    app.router.add_post('/api/users/{user_id}/ban', api_ban)
    app.router.add_post('/api/users/{user_id}/unban', api_unban)
    app.router.add_post('/api/subscriptions', api_subscribe)
    app.router.add_post('/api/subscriptions/remove', api_unsubscribe)
    app.router.add_post('/api/intents', api_send)
    app.router.add_post('/api/populate', api_populate)
    app.router.add_get('/health', health)
    app.router.add_get('/sync/heartbeat', sync_heartbeat)
    app.router.add_get('/sync/changes', sync_changes)
    app.router.add_get('/sync/snapshot', sync_snapshot)
    app.router.add_post('/sync/handoff', sync_handoff)

    return app


def _persist_generated_secret(state: BotState, secret: str, filename: str, env_var: str, purpose: str):
    secret_file = Path(state.config.config_folder) / 'data' / filename
    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(secret + '\n')
        secret_file.chmod(0o600)
        logger.info('%s not set, generated value written to %s', env_var, secret_file)
    except OSError:
        logger.warning(
            '%s not set and writing %s failed; set %s to %s',
            env_var, secret_file, env_var, purpose, exc_info=True,
        )


async def start_dashboard(state: BotState, ha=None):
    if not os.getenv('DASHBOARD_PASSWORD'):
        _persist_generated_secret(state, get_password(), 'dashboard_password.txt',
                                  'DASHBOARD_PASSWORD', 'log in to the dashboard')
    if not os.getenv('API_TOKEN'):
        _persist_generated_secret(state, get_api_token(), 'api_token.txt',
                                  'API_TOKEN', 'authorize /api/ requests')
    if not os.getenv('ADMIN_TOKEN'):
        _persist_generated_secret(state, get_admin_token(), 'admin_token.txt',
                                  'ADMIN_TOKEN', 'authorize admin /api/ requests')

    port = int(os.getenv('DASHBOARD_PORT', '8080'))
    vpn = None
    if state.config is not None and state.config.vpn_control_apikey:
        vpn = VpnMonitor(state.config.vpn_control_url, state.config.vpn_control_apikey)
    app = create_app(state, ha, vpn)
    if vpn is not None:
        # Owned by the app, not state.background_tasks: it describes this host
        # and must survive a step-down, which cancels every background task
        app[VPN_TASK_KEY] = asyncio.create_task(run_forever(vpn.run, 'vpn_monitor'), name='vpn_monitor')
        logger.info('VPN status poller started against %s', vpn.url)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info('Dashboard started on port %d', port)
