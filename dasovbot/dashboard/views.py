from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import TYPE_CHECKING

import aiohttp_jinja2
from aiohttp import web

from dasovbot.constants import DATETIME_FORMAT
from dasovbot.services.background import run_populate_subscriptions
from dasovbot.services.intent_processor import filter_intents

if TYPE_CHECKING:
    from dasovbot.state import BotState


STATE_KEY = web.AppKey('state')
HA_KEY = web.AppKey('ha')


def get_state(request: web.Request) -> BotState:
    return request.app[STATE_KEY]


def get_ha(request: web.Request):
    """The RoleController, or None (tests, preview_dashboard.py, pre-HA callers)."""
    return request.app.get(HA_KEY)


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


async def health_alerts_processor(request: web.Request) -> dict:
    """Expose active health alerts to every template so base.html can render a
    banner on all authenticated pages."""
    if STATE_KEY not in request.app:
        return {'health_alerts': []}
    state = request.app[STATE_KEY]
    alerts = [
        {
            'id': alert_id,
            'level': info.get('level', 'warning'),
            'message': info.get('message', ''),
            'since': relative_time(info.get('since')),
        }
        for alert_id, info in state.health_alerts.items()
    ]
    return {'health_alerts': alerts}


def user_name(state: BotState, user_id: str) -> str:
    data = state.users.get(user_id) or {}
    name = ' '.join(p for p in [data.get('first_name'), data.get('last_name')] if p)
    if data.get('username'):
        name = f"{name} @{data['username']}".strip()
    if not name:
        # Name captured at ban time, for users never stored in state.users
        name = (state.banned_users.get(user_id) or {}).get('name', '')
    return name


def user_label(state: BotState, user_id: str) -> str:
    name = user_name(state, user_id)
    return f'{name} ({user_id})' if name else user_id


def user_item(state: BotState, user_id: str) -> dict:
    return {'id': user_id, 'label': user_label(state, user_id), 'banned': state.is_banned(user_id)}


# Browsers read a backslash as a slash and drop tabs and newlines, so a
# backslash or a tab after the leading slash resolves like '//host'
_UNSAFE_NEXT = re.compile(r'[\\\t\r\n]')


def safe_next(target: str, default: str) -> str:
    # Local paths only: '//host' would redirect off-site
    if target.startswith('/') and not target.startswith('//') and not _UNSAFE_NEXT.search(target):
        return target
    return default


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
    try:
        page = max(1, int(request.query.get('page', '1')))
    except ValueError:
        page = 1
    per_page = 50
    search_query = request.query.get('q', '').strip()
    # Exact requester id, unlike q which is a substring match on labels too
    user_filter = request.query.get('user', '').strip()

    items = []
    q_lower = search_query.lower()
    labels: dict[str, str] = {}
    for url, info in state.videos.items():
        if not info.file_id:
            continue
        if source_filter != 'all' and info.source != source_filter:
            continue
        # Requests are logged under the URL the user sent, which may be the
        # video's alternate key rather than this row's
        requester_ids = list(dict.fromkeys(
            state.video_requesters.get(url, []) + state.video_requesters.get(info.webpage_url, [])
        ))
        if user_filter and user_filter not in requester_ids:
            continue
        if q_lower:
            for user_id in requester_ids:
                if user_id not in labels:
                    labels[user_id] = user_label(state, user_id)
            searchable = '\n'.join(filter(None, [
                info.title,
                info.webpage_url or url,
                url,
                info.upload_date,
                info.processed_at,
                info.caption,
                info.description,
                info.uploader_url,
                *(labels[user_id] for user_id in requester_ids),
            ]))
            if q_lower not in searchable.lower():
                continue
        items.append({
            'url': url,
            'title': info.title,
            'webpage_url': info.webpage_url or url,
            'upload_date': info.upload_date or '',
            'processed_at': info.processed_at or '',
            'source': info.source or '',
            'duration': info.duration,
            'requester_ids': requester_ids,
        })

    if sort_by == 'upload_date':
        items.sort(key=lambda x: x['upload_date'], reverse=True)
    else:
        items.sort(key=lambda x: x['processed_at'], reverse=True)

    total_items = len(items)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    page = min(page, total_pages)
    items = items[(page - 1) * per_page : page * per_page]
    # Requester rows (name lookups, ban state) only for the page shown
    for item in items:
        item['requesters'] = [user_item(state, user_id) for user_id in item.pop('requester_ids')]

    context = {
        'videos': items,
        'sort_by': sort_by,
        'source_filter': source_filter,
        'page': page,
        'total_pages': total_pages,
        'total_items': total_items,
        'search_query': search_query,
        'user_filter': user_filter,
        'user_filter_label': user_label(state, user_filter) if user_filter else '',
    }
    return aiohttp_jinja2.render_template('videos.html', request, context)


async def ignored(request: web.Request) -> web.Response:
    state = get_state(request)

    items = []
    for url, intent in state.intents.items():
        if intent.ignored:
            video = state.videos.get(url)
            items.append({
                'url': url,
                'title': intent.title or (video.title if video else '') or url,
                'source': intent.source or '',
                'type': 'intent',
            })
    for url, tiq in state.temporary_inline_queries.items():
        if tiq.ignored:
            title = tiq.title or url
            for result in tiq.results:
                if hasattr(result, 'title') and result.title:
                    title = result.title
                    break
            items.append({
                'url': url,
                'title': title,
                'source': 'inline',
                'type': 'inline',
            })

    return aiohttp_jinja2.render_template('ignored.html', request, {'items': items})


async def retry_ignored(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await request.post()
    url = data.get('url', '')
    item_type = data.get('type', '')

    if url:
        if item_type == 'intent' and url in state.intents:
            state.intents[url].ignored = False
            # A manual retry is an explicit request: skip any failure backoff
            state.intent_retry_after.pop(url, None)
            await state.save_intent(url)
            state.download_queue.put_nowait(url)
        elif item_type == 'inline' and url in state.temporary_inline_queries:
            state.temporary_inline_queries[url].ignored = False

    raise web.HTTPFound('/ignored')


async def remove_ignored(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await request.post()
    url = data.get('url', '')
    item_type = data.get('type', '')

    if url:
        if item_type == 'intent':
            await state.pop_intent(url)
        elif item_type == 'inline':
            state.temporary_inline_queries.pop(url, None)

    raise web.HTTPFound('/ignored')


async def remove_intent(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await request.post()
    url = data.get('url', '')
    if url:
        await state.pop_intent(url)
    raise web.HTTPFound('/')


async def force_populate(request: web.Request) -> web.Response:
    state = get_state(request)
    task = asyncio.create_task(run_populate_subscriptions(state))
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)
    referer = request.headers.get('Referer', '')
    redirect = '/' if referer.endswith('/') else '/system'
    raise web.HTTPFound(redirect)


USER_COLORS = [
    '#e94560', '#53a8e2', '#95d5b2', '#d4a5d0', '#f9c74f',
    '#f3722c', '#43aa8b', '#577590', '#f8961e', '#90be6d',
    '#4cc9f0', '#7209b7', '#3a86ff', '#ff006e', '#8338ec',
]


async def subscriptions(request: web.Request) -> web.Response:
    state = get_state(request)

    all_chat_ids = sorted({cid for sub in state.subscriptions.values() for cid in sub.chat_ids})
    color_map = {cid: USER_COLORS[i % len(USER_COLORS)] for i, cid in enumerate(all_chat_ids)}

    users = []
    for cid in all_chat_ids:
        user_data = state.users.get(cid, {})
        parts = [p for p in [user_data.get('first_name', ''), user_data.get('last_name', '')] if p]
        label = ' '.join(parts)
        if label:
            label = f'{label} ({cid})'
        else:
            label = cid
        users.append({'id': cid, 'color': color_map[cid], 'label': label})

    items = []
    for url, sub in sorted(state.subscriptions.items(), key=lambda x: x[1].title.lower()):
        items.append({
            'url': url,
            'title': sub.title or sub.uploader or url,
            'uploader': sub.uploader,
            'chat_ids': sub.chat_ids,
        })

    user_labels = {u['id']: u['label'] for u in users}

    context = {
        'users': users,
        'subscriptions': items,
        'color_map': color_map,
        'user_labels': user_labels,
    }
    return aiohttp_jinja2.render_template('subscriptions.html', request, context)


async def remove_subscription(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await request.post()
    url = data.get('url', '')
    chat_id = data.get('chat_id', '')
    if url:
        if chat_id:
            await state.remove_subscriber(url, chat_id)
        else:
            await state.pop_subscription(url)
    raise web.HTTPFound('/subscriptions')


async def users(request: web.Request) -> web.Response:
    state = get_state(request)
    items = []
    for user_id in set(state.user_requests) | set(state.banned_users):
        stats = state.user_requests.get(user_id) or {}
        item = user_item(state, user_id)
        item['count'] = stats.get('count', 0)
        item['last_at'] = stats.get('last_at') or ''
        item['last_at_relative'] = relative_time(item['last_at'])
        item['banned_at'] = (state.banned_users.get(user_id) or {}).get('banned_at', '')
        items.append(item)
    items.sort(key=lambda x: (x['count'], x['last_at']), reverse=True)
    return aiohttp_jinja2.render_template('users.html', request, {'users': items})


async def ban_user(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await request.post()
    user_id = data.get('user_id', '').strip()
    if user_id.isdigit():
        await state.ban_user(user_id, user_name(state, user_id))
    raise web.HTTPFound(safe_next(data.get('next', ''), '/users'))


async def unban_user(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await request.post()
    user_id = data.get('user_id', '').strip()
    if user_id:
        await state.unban_user(user_id)
    raise web.HTTPFound(safe_next(data.get('next', ''), '/users'))


async def system(request: web.Request) -> web.Response:
    state = get_state(request)

    tasks = [
        {'name': 'populate_subscriptions', 'description': 'Checks subscriptions for new videos', 'interval': '1 hour'},
        {'name': 'clear_temporary_inline_queries', 'description': 'Cleans up stale inline queries', 'interval': '10 min'},
        {'name': 'monitor_process_intents', 'description': 'Processes download queue', 'interval': 'continuous'},
        {'name': 'sweep_media_folder', 'description': 'Removes leftover media files older than 6 hours', 'interval': '1 hour'},
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
        'migration': state.migration_progress,
    }
    return aiohttp_jinja2.render_template('system.html', request, context)
