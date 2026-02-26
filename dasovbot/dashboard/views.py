from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import aiohttp_jinja2
from aiohttp import web

from dasovbot.constants import DATETIME_FORMAT
from dasovbot.services.intent_processor import filter_intents

if TYPE_CHECKING:
    from dasovbot.state import BotState


def get_state(request: web.Request) -> BotState:
    return request.app['state']


def parse_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, DATETIME_FORMAT)
    except (ValueError, TypeError):
        return None


def relative_time(ts: str | None) -> str:
    dt = parse_timestamp(ts)
    if not dt:
        return 'never'
    delta = datetime.now() - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f'{seconds}s ago'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes}m ago'
    hours = minutes // 60
    if hours < 24:
        return f'{hours}h ago'
    days = hours // 24
    return f'{days}d ago'


async def index(request: web.Request) -> web.Response:
    state = get_state(request)

    filtered = filter_intents(state.intents)
    intents = []
    for url, intent in sorted(filtered.items(), key=lambda x: x[1].priority, reverse=True):
        video = state.videos.get(url)
        intents.append({
            'url': url,
            'title': intent.title or (video.title if video else ''),
            'upload_date': intent.upload_date or (video.upload_date or '' if video else ''),
            'priority': intent.priority,
            'chat_ids_count': len(intent.chat_ids),
            'inline_msg_ids_count': len(intent.inline_message_ids),
            'messages_count': len(intent.messages),
            'source': intent.source or '',
        })

    context = {
        'video_count': len(state.videos),
        'subscription_count': len(state.subscriptions),
        'intent_count': len(filtered),
        'user_count': len(state.users),
        'intents': intents,
    }
    return aiohttp_jinja2.render_template('index.html', request, context)


async def videos(request: web.Request) -> web.Response:
    state = get_state(request)
    sort_by = request.query.get('sort', 'processed_at')
    source_filter = request.query.get('source', 'all')
    limit = int(request.query.get('limit', '50'))

    items = []
    for url, info in state.videos.items():
        if not info.file_id:
            continue
        if source_filter != 'all' and info.source != source_filter:
            continue
        items.append({
            'url': url,
            'title': info.title,
            'webpage_url': info.webpage_url or url,
            'upload_date': info.upload_date or '',
            'processed_at': info.processed_at or '',
            'source': info.source or '',
            'duration': info.duration,
        })

    if sort_by == 'upload_date':
        items.sort(key=lambda x: x['upload_date'], reverse=True)
    else:
        items.sort(key=lambda x: x['processed_at'], reverse=True)

    items = items[:limit]

    context = {
        'videos': items,
        'sort_by': sort_by,
        'source_filter': source_filter,
        'limit': limit,
    }
    return aiohttp_jinja2.render_template('videos.html', request, context)


async def system(request: web.Request) -> web.Response:
    state = get_state(request)

    tasks = [
        {'name': 'populate_subscriptions', 'description': 'Checks subscriptions for new videos', 'interval': '1 hour'},
        {'name': 'clear_temporary_inline_queries', 'description': 'Cleans up stale inline queries', 'interval': '10 min'},
        {'name': 'monitor_process_intents', 'description': 'Processes download queue', 'interval': 'continuous'},
    ]
    for task in tasks:
        last_run = state.background_task_status.get(task['name'], '')
        task['last_run'] = last_run
        task['last_run_relative'] = relative_time(last_run)

    context = {
        'tasks': tasks,
        'video_count': len(state.videos),
        'subscription_count': len(state.subscriptions),
        'user_count': len(state.users),
        'intent_count': len(state.intents),
        'tiq_count': len(state.temporary_inline_queries),
        'queue_size': state.download_queue.qsize(),
    }
    return aiohttp_jinja2.render_template('system.html', request, context)
