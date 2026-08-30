from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp_jinja2
import jinja2
from aiohttp import web

from dasovbot.dashboard.api import api_video, api_videos
from dasovbot.dashboard.auth import auth_middleware, login_page, login_post, logout, get_password, get_api_token
from dasovbot.dashboard.views import index, videos, ignored, retry_ignored, remove_ignored, remove_intent, force_populate, subscriptions, remove_subscription, system, health_alerts_processor, STATE_KEY

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


def create_app(state: BotState) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app[STATE_KEY] = state

    env = aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        context_processors=[health_alerts_processor, aiohttp_jinja2.request_processor],
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
    app.router.add_post('/system/populate', force_populate)
    app.router.add_get('/system', system)
    app.router.add_get('/api/videos', api_videos)
    app.router.add_get('/api/videos/{video_id}', api_video)

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


async def start_dashboard(state: BotState):
    if not os.getenv('DASHBOARD_PASSWORD'):
        _persist_generated_secret(state, get_password(), 'dashboard_password.txt',
                                  'DASHBOARD_PASSWORD', 'log in to the dashboard')
    if not os.getenv('API_TOKEN'):
        _persist_generated_secret(state, get_api_token(), 'api_token.txt',
                                  'API_TOKEN', 'authorize /api/ requests')

    port = int(os.getenv('DASHBOARD_PORT', '8080'))
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info('Dashboard started on port %d', port)
