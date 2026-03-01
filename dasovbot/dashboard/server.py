from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp_jinja2
import jinja2
from aiohttp import web

from dasovbot.dashboard.auth import auth_middleware, login_page, login_post, logout, get_password
from dasovbot.dashboard.views import index, videos, ignored, remove_ignored, system

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


def create_app(state: BotState) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app['state'] = state

    env = aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)))
    env.filters['duration'] = format_duration

    app.router.add_static('/static', STATIC_DIR, name='static')
    app.router.add_get('/login', login_page)
    app.router.add_post('/login', login_post)
    app.router.add_get('/logout', logout)
    app.router.add_get('/', index)
    app.router.add_get('/videos', videos)
    app.router.add_get('/ignored', ignored)
    app.router.add_post('/ignored/remove', remove_ignored)
    app.router.add_get('/system', system)

    return app


async def start_dashboard(state: BotState):
    if not os.getenv('DASHBOARD_PASSWORD'):
        password = get_password()
        logger.info('DASHBOARD_PASSWORD not set, generated password: %s', password)

    port = int(os.getenv('DASHBOARD_PORT', '8080'))
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info('Dashboard started on port %d', port)
