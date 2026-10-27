"""Admin JSON API under `/api/` (bearer ADMIN_TOKEN): what an operator or agent
needs to look at before acting. Bare objects/arrays on success, `{error, detail}`
on failure, exactly like the videos API in `api.py` (which keeps its own
read-only contract)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING

from aiohttp import web

from dasovbot.constants import SOURCE_API
from dasovbot.dashboard.api import collect_entries, parse_bool
from dasovbot.dashboard.sync import health_body
from dasovbot.dashboard.views import get_ha, get_state, user_label, user_name
from dasovbot.handlers.subscription import discover_playlists, resolve_subscription
from dasovbot.helpers import now
from dasovbot.models import Subscription
from dasovbot.services.background import (
    BACKGROUND_TASK_NAMES, newest_backup_age, populate_playlist, populate_running, run_populate_subscriptions,
)
from dasovbot.services.intent_processor import append_intent

if TYPE_CHECKING:
    from dasovbot.models import Intent
    from dasovbot.state import BotState

MAX_CANDIDATES = 5


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


# --- Writes (ADMIN_TOKEN; the role guard answers 409 on a passive node) ---
#
# Every write resolves its target first and, with `dry_run: true`, stops right
# before the state call with the same resolution result. Ban, unban and
# unsubscribe post one audit line to the developer chat; subscribe and send do
# not (the admin sees those in Telegram anyway).


def _bad_request(message: str, detail: str = '') -> web.HTTPBadRequest:
    return web.HTTPBadRequest(text=json.dumps({'error': message, 'detail': detail}), content_type='application/json')


async def read_json(request: web.Request) -> dict:
    """The JSON object body, `{}` when there is none; 400 for anything else."""
    if not request.can_read_body:
        return {}
    text = await request.text()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _bad_request('invalid json', str(exc))
    if not isinstance(data, dict):
        raise _bad_request('invalid body', 'body must be a JSON object')
    return data


def parse_flag(data: dict, name: str) -> bool:
    value = data.get(name, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return parse_bool(value)
    return bool(value)


def _candidates(state: BotState, user_ids, needle: str = '') -> list[dict]:
    needle = needle.lower()
    rows = []
    for user_id in user_ids:
        entry = user_entry(state, user_id)
        if not needle or needle in entry['name'].lower() or needle in (entry['username'] or '').lower():
            rows.append(entry)
    rows.sort(key=lambda item: (item['requests'], item['last_at'] or ''), reverse=True)
    return rows[:MAX_CANDIDATES]


def _known_ids(state: BotState) -> set[str]:
    known = set(state.users) | set(state.user_requests) | set(state.banned_users)
    developer = getattr(state.config, 'developer_chat_id', '') if state.config else ''
    if developer:
        # The default target is a valid explicit one too
        known.add(str(developer))
    return known


def _prefix_candidates(state: BotState, raw: str) -> list[dict]:
    """Known ids sharing the longest common prefix with a mistyped id, best first."""
    def common(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n
    scored = sorted(((common(raw, user_id), user_id) for user_id in _known_ids(state)), reverse=True)
    return [user_entry(state, user_id) for score, user_id in scored[:MAX_CANDIDATES] if score > 0]


def resolve_user(state: BotState, raw: str) -> tuple[str | None, web.Response | None]:
    """A numeric id as is (never-seen ids may be banned, as on the dashboard),
    or `@username` looked up in the stored users: 404 unknown, 409 ambiguous."""
    raw = (raw or '').strip()
    if raw.isdigit():
        return raw, None
    if raw.startswith('@') and len(raw) > 1:
        needle = raw[1:].lower()
        matches = [user_id for user_id, data in state.users.items()
                   if (data.get('username') or '').lower() == needle]
        if len(matches) == 1:
            return matches[0], None
        if not matches:
            return None, error(404, 'unknown user', f'no stored user with username {raw}',
                               candidates=_candidates(state, state.users, needle))
        return None, error(409, 'ambiguous user', f'{len(matches)} stored users share the username {raw}',
                           candidates=_candidates(state, matches))
    return None, error(400, 'invalid user', 'pass a numeric id or @username')


def resolve_chat(state: BotState, raw, force: bool = False) -> tuple[str | None, web.Response | None]:
    """Target chat: the developer chat by default, else a known id or @username.
    An unknown numeric id is refused with the closest known ids unless `force`."""
    raw = str(raw).strip() if raw is not None else ''
    if not raw:
        default = getattr(state.config, 'developer_chat_id', '') if state.config else ''
        if not default:
            return None, error(400, 'chat_id required', 'no DEVELOPER_CHAT_ID to default to')
        return str(default), None
    if raw.startswith('@'):
        return resolve_user(state, raw)
    if raw.lstrip('-').isdigit():
        if force or raw in _known_ids(state):
            return raw, None
        return None, error(404, 'unknown chat', 'pass force: true to target an unknown chat id',
                           candidates=_prefix_candidates(state, raw))
    return None, error(400, 'invalid chat', 'pass a numeric chat id or @username')


async def audit(request: web.Request, text: str):
    notifier = getattr(get_ha(request), 'notifier', None)
    if notifier is not None:
        await notifier.notify(text)


def _spawn(state: BotState, coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)
    return task


async def _ban_action(request: web.Request, ban: bool) -> web.Response:
    state = get_state(request)
    data = await read_json(request)
    user_id, failure = resolve_user(state, request.match_info['user_id'])
    if failure is not None:
        return failure
    action = 'ban' if ban else 'unban'
    user = user_entry(state, user_id)
    if parse_flag(data, 'dry_run'):
        return web.json_response({'dry_run': True, 'action': action, 'user': user})
    if user['banned'] == ban:
        return web.json_response({'action': action, 'user': user, 'changed': False})
    if ban:
        await state.ban_user(user_id, user_name(state, user_id))
    else:
        await state.unban_user(user_id)
    await audit(request, f"API: {'banned' if ban else 'unbanned'} {user_label(state, user_id)}")
    return web.json_response({'action': action, 'user': user_entry(state, user_id), 'changed': True})


async def api_ban(request: web.Request) -> web.Response:
    return await _ban_action(request, ban=True)


async def api_unban(request: web.Request) -> web.Response:
    return await _ban_action(request, ban=False)


def _valid_url(value) -> str | None:
    if isinstance(value, str) and value.strip().startswith(('http://', 'https://')):
        return value.strip()
    return None


async def _plan_subscription(url: str) -> tuple[str, Subscription] | web.Response:
    """The stored key and record a new subscription to `url` would get.

    Same resolution as the Telegram flow: a channel (or a channel without a
    playlists tab) means its Videos feed; a URL that is one of the channel's
    feeds or playlists means that entry; a URL yt-dlp cannot place on a
    channel is subscribed to as is. A video URL is refused with the choices.
    """
    try:
        found = await discover_playlists(url)
    except Exception:  # noqa: BLE001 — not a channel: subscribe to the URL itself
        try:
            return url, await resolve_subscription(url)
        except Exception as exc:  # noqa: BLE001 — yt-dlp raises anything; the caller gets the text
            return error(502, 'extraction failed', str(exc))
    uploader_url = found['uploader_url']
    if not uploader_url:
        return error(400, 'unsupported url', 'yt-dlp found no uploader for this url')
    uploader = found['uploader'] or ''
    uploader_videos = found['uploader_videos']
    if found['fallback_videos']:
        try:
            return uploader_videos, await resolve_subscription(uploader_videos)
        except Exception as exc:  # noqa: BLE001
            return error(502, 'extraction failed', str(exc))
    if url.rstrip('/') == uploader_url.rstrip('/'):
        return uploader_videos, Subscription(title=f"{uploader} Videos", uploader=uploader, uploader_videos=uploader_videos)
    for item in found['playlists'].values():
        if item['url'] == url:
            return url, Subscription(title=item['title'], uploader=uploader, uploader_videos=uploader_videos)
    return error(409, 'ambiguous url', 'url is neither a channel nor one of its feeds or playlists; subscribe to one of `playlists`',
                 uploader=uploader, uploader_url=uploader_url,
                 playlists=[{'title': item['title'], 'url': item['url']} for item in found['playlists'].values()])


def _subscription_body(url: str, sub: Subscription) -> dict:
    return {'url': url, 'title': sub.title, 'uploader': sub.uploader, 'uploader_videos': sub.uploader_videos,
            'chat_ids': list(sub.chat_ids)}


async def api_subscribe(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await read_json(request)
    url = _valid_url(data.get('url'))
    if not url:
        return error(400, 'invalid url', 'url must be an http(s) URL')
    chat_id, failure = resolve_chat(state, data.get('chat_id'), parse_flag(data, 'force'))
    if failure is not None:
        return failure
    dry_run = parse_flag(data, 'dry_run')
    existing = state.subscriptions.get(url)
    if existing is not None:
        if chat_id in existing.chat_ids:
            return web.json_response({'status': 'already_subscribed', 'url': url,
                                      'subscription': _subscription_body(url, existing)})
        action, target, record = 'add_subscriber', url, existing
        preview = Subscription(chat_ids=existing.chat_ids + [chat_id], title=existing.title,
                               uploader=existing.uploader, uploader_videos=existing.uploader_videos)
    else:
        planned = await _plan_subscription(url)
        if isinstance(planned, web.Response):
            return planned
        target, record = planned
        if target != url and chat_id in (state.subscriptions.get(target) or Subscription()).chat_ids:
            # A bare channel URL resolved to a Videos feed this chat already has
            return web.json_response({'status': 'already_subscribed', 'url': target,
                                      'subscription': _subscription_body(target, state.subscriptions[target])})
        if target in state.subscriptions:
            action = 'add_subscriber'
            record = state.subscriptions[target]
            preview = Subscription(chat_ids=record.chat_ids + [chat_id], title=record.title,
                                   uploader=record.uploader, uploader_videos=record.uploader_videos)
        else:
            action = 'subscribe'
            preview = Subscription(chat_ids=[chat_id], title=record.title, uploader=record.uploader,
                                   uploader_videos=record.uploader_videos)
    if dry_run:
        return web.json_response({'dry_run': True, 'action': action, 'url': target,
                                  'subscription': _subscription_body(target, preview)})
    if action == 'add_subscriber':
        await state.add_subscriber(target, chat_id)
    else:
        await state.set_subscription(target, preview)
    populate = 'skipped'
    if parse_flag(data, 'populate'):
        # Queues the latest videos for this chat through populate_video → append_intent
        _spawn(state, populate_playlist(target, [chat_id], state))
        populate = 'started'
    return web.json_response({'action': action, 'url': target,
                              'subscription': _subscription_body(target, state.subscriptions[target]),
                              'populate': populate})


async def api_unsubscribe(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await read_json(request)
    url = data.get('url')
    sub = state.subscriptions.get(url) if isinstance(url, str) else None
    if sub is None:
        return error(404, 'not subscribed', 'url is not a stored subscription key')
    chat_id, failure = resolve_chat(state, data.get('chat_id'), parse_flag(data, 'force'))
    if failure is not None:
        return failure
    if chat_id not in sub.chat_ids:
        return error(404, 'chat not subscribed', f'{chat_id} is not a subscriber of this url')
    last = len(sub.chat_ids) == 1
    if parse_flag(data, 'dry_run'):
        return web.json_response({'dry_run': True, 'action': 'remove_subscriber', 'url': url,
                                  'chat_id': chat_id, 'last_subscriber': last})
    title = sub.title or sub.uploader or url
    await state.remove_subscriber(url, chat_id)
    removed = url not in state.subscriptions
    await audit(request, f"API: unsubscribed {user_label(state, chat_id)} from {title}"
                         + (' (subscription removed)' if removed else ''))
    return web.json_response({'action': 'remove_subscriber', 'url': url, 'chat_id': chat_id,
                              'removed_subscription': removed})


async def api_send(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await read_json(request)
    url = _valid_url(data.get('url'))
    if not url:
        return error(400, 'invalid url', 'url must be an http(s) URL')
    chat_id, failure = resolve_chat(state, data.get('chat_id'), parse_flag(data, 'force'))
    if failure is not None:
        return failure
    if state.is_banned(chat_id):
        # The worker would skip a banned requester at delivery; failing early is clearer
        return error(403, 'chat banned', f'{chat_id} is banned and would never receive the video')
    video = state.videos.get(url)
    cached = bool(video is not None and video.file_id)
    if parse_flag(data, 'dry_run'):
        return web.json_response({'dry_run': True, 'action': 'queue', 'url': url, 'chat_id': chat_id,
                                  'cached': cached, 'existing_intent': url in state.intents})
    await append_intent(url, state, chat_ids=[chat_id], source=SOURCE_API)
    return web.json_response({'action': 'queue', 'url': url, 'chat_id': chat_id, 'cached': cached,
                              'intent': intent_entry(state, url, state.intents[url])})


async def api_populate(request: web.Request) -> web.Response:
    state = get_state(request)
    data = await read_json(request)
    url = data.get('url')
    if url:
        sub = state.subscriptions.get(url) if isinstance(url, str) else None
        if sub is None:
            return error(404, 'not subscribed', 'url is not a stored subscription key')
        _spawn(state, populate_playlist(url, list(sub.chat_ids), state))
        return web.json_response({'status': 'started', 'scope': url})
    if populate_running():
        return web.json_response({'status': 'already_running', 'scope': 'all'})
    _spawn(state, run_populate_subscriptions(state))
    return web.json_response({'status': 'started', 'scope': 'all'})
