"""Admin JSON API under `/api/` (bearer ADMIN_TOKEN): what an operator or agent
needs to look at before acting. Bare objects/arrays on success, `{error, detail}`
on failure, exactly like the videos API in `api.py` (which keeps its own
read-only contract)."""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from aiohttp import web

from dasovbot.dashboard.api import collect_entries, parse_bool
from dasovbot.dashboard.sync import health_body
from dasovbot.dashboard.views import get_state, user_name
from dasovbot.handlers.subscription import discover_playlists
from dasovbot.helpers import now
from dasovbot.services.background import BACKGROUND_TASK_NAMES, newest_backup_age

if TYPE_CHECKING:
    from dasovbot.models import Intent, Subscription
    from dasovbot.state import BotState


def error(status: int, message: str, detail: str = '', **extra) -> web.Response:
    return web.json_response({'error': message, 'detail': detail, **extra}, status=status)


def user_entry(state: BotState, user_id: str) -> dict:
    data = state.users.get(user_id) or {}
    name = ' '.join(part for part in [data.get('first_name'), data.get('last_name')] if part)
    banned = state.banned_users.get(user_id) or {}
    if not name:
        # Name captured at ban time, for users never stored in state.users
        name = banned.get('name', '')
    stats = state.user_requests.get(user_id) or {}
    return {
        'id': user_id,
        'name': name,
        'username': data.get('username') or None,
        'banned': state.is_banned(user_id),
        'banned_at': banned.get('banned_at'),
        'requests': stats.get('count', 0),
        'last_at': stats.get('last_at'),
    }


def _retry_in_sec(state: BotState, url: str) -> int:
    deadline = state.intent_retry_after.get(url)
    if deadline is None:
        return 0
    return max(0, round(deadline - time.monotonic()))


def intent_status(state: BotState, url: str, intent: Intent) -> str:
    if intent.ignored:
        return 'ignored'
    if intent.retries > 0 or state.intent_retry_after.get(url, 0) > time.monotonic():
        return 'retrying'
    return 'pending'


def intent_entry(state: BotState, url: str, intent: Intent) -> dict:
    video = state.videos.get(url)
    return {
        'url': url,
        'status': intent_status(state, url, intent),
        'priority': intent.priority,
        'retries': intent.retries,
        'source': intent.source,
        'title': intent.title or (video.title if video else None),
        'upload_date': intent.upload_date or (video.upload_date if video else None),
        'chat_ids': list(intent.chat_ids),
        'inline_count': len(intent.inline_message_ids),
        'message_count': len(intent.messages),
        'retry_in_sec': _retry_in_sec(state, url),
    }


def subscription_entry(state: BotState, url: str, sub: Subscription) -> dict:
    return {
        'url': url,
        'title': sub.title,
        'uploader': sub.uploader,
        'uploader_videos': sub.uploader_videos,
        'chat_ids': [{'id': chat_id, 'name': user_name(state, chat_id)} for chat_id in sub.chat_ids],
    }


def backup_status(state: BotState) -> dict | None:
    if state.config is None:
        return None
    backup_dir = os.path.dirname(state.config.db_file) or '.'
    age = newest_backup_age(backup_dir)
    return {'dir': backup_dir, 'newest_age_sec': None if age is None else round(age)}


async def api_status(request: web.Request) -> web.Response:
    state = get_state(request)
    body = health_body(request)
    intents = {'pending': 0, 'retrying': 0, 'ignored': 0}
    for url, intent in state.intents.items():
        intents[intent_status(state, url, intent)] += 1
    intents['total'] = len(state.intents)
    body['counts'] = {
        'users': len(state.users),
        'banned': len(state.banned_users),
        # Deduplicated, delivered videos: the number the videos API serves
        'videos': len(collect_entries(state)),
        'videos_rows': len(state.videos),
        'subscriptions': len(state.subscriptions),
        'subscribers': sum(len(sub.chat_ids) for sub in state.subscriptions.values()),
        'intents': intents,
        'inline_queries': len(state.temporary_inline_queries),
        'alerts': len(state.health_alerts),
    }
    body['tasks'] = {name: state.background_task_status.get(name) or None for name in BACKGROUND_TASK_NAMES}
    body['backup'] = backup_status(state)
    body['migration'] = state.migration_progress
    body['queue_size'] = state.download_queue.qsize()
    body['vpn'] = None
    body['node_time'] = now()
    return web.json_response(body)


def _matches(entry: dict, search: str) -> bool:
    needle = search.lower().lstrip('@')
    if not needle:
        return True
    haystack = [entry['id'], entry['name'].lower(), (entry['username'] or '').lower()]
    return any(needle in item for item in haystack)


async def api_users(request: web.Request) -> web.Response:
    state = get_state(request)
    search = request.query.get('search', '').strip()
    banned = request.query.get('banned')
    items = []
    for user_id in set(state.user_requests) | set(state.banned_users) | set(state.users):
        entry = user_entry(state, user_id)
        if banned is not None and entry['banned'] != parse_bool(banned):
            continue
        if search and not _matches(entry, search):
            continue
        items.append(entry)
    items.sort(key=lambda item: (item['requests'], item['last_at'] or ''), reverse=True)
    return web.json_response(items)


async def api_subscriptions(request: web.Request) -> web.Response:
    state = get_state(request)
    chat_id = request.query.get('chat_id', '').strip()
    items = [
        subscription_entry(state, url, sub)
        for url, sub in sorted(state.subscriptions.items(), key=lambda item: item[1].title.lower())
        if not chat_id or chat_id in sub.chat_ids
    ]
    return web.json_response(items)


async def api_intents(request: web.Request) -> web.Response:
    state = get_state(request)
    url = request.query.get('url', '').strip()
    if url:
        # The lookup `send --wait` polls: a delivered intent is popped, so its
        # absence plus a cached file_id means done, absence alone means dropped
        intent = state.intents.get(url)
        if intent is not None:
            entry = intent_entry(state, url, intent)
            return web.json_response({'url': url, 'status': entry['status'], 'intent': entry})
        video = state.videos.get(url)
        status = 'delivered' if video is not None and video.file_id else 'absent'
        return web.json_response({'url': url, 'status': status, 'intent': None})
    ignored = request.query.get('ignored')
    items = [
        intent_entry(state, key, intent)
        for key, intent in sorted(state.intents.items(), key=lambda item: item[1].priority, reverse=True)
        if ignored is None or intent.ignored == parse_bool(ignored)
    ]
    return web.json_response(items)


async def api_alerts(request: web.Request) -> web.Response:
    state = get_state(request)
    items = [
        {
            'id': alert_id,
            'level': info.get('level', 'warning'),
            'message': info.get('message', ''),
            'since': info.get('since'),
        }
        for alert_id, info in state.health_alerts.items()
    ]
    return web.json_response(items)


async def api_playlists(request: web.Request) -> web.Response:
    url = request.query.get('url', '').strip()
    if not url.startswith(('http://', 'https://')):
        return error(400, 'invalid url', 'url must be an http(s) URL')
    try:
        found = await discover_playlists(url)
    except Exception as exc:  # noqa: BLE001 — yt-dlp raises anything; the caller gets the text
        return error(502, 'extraction failed', str(exc))
    if not found['uploader_url']:
        return error(400, 'unsupported url', 'yt-dlp found no uploader for this url')
    return web.json_response({
        'uploader': found['uploader'],
        'uploader_url': found['uploader_url'],
        'uploader_videos': found['uploader_videos'],
        'playlists': [{'title': item['title'], 'url': item['url']} for item in found['playlists'].values()],
        'direct': found['direct'],
        'fallback_videos': found['fallback_videos'],
    })
