from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from aiohttp import web

from dasovbot.dashboard.views import get_state

if TYPE_CHECKING:
    from dasovbot.models import VideoInfo
    from dasovbot.state import BotState


def video_entry(info: VideoInfo) -> dict:
    # Key names mirror a sidecar-built library index (camelCase, chapter
    # 'start' in seconds, 'fetchedAt' in epoch seconds), so index consumers
    # can parse API entries unchanged
    return {
        'id': info.video_id,
        'title': info.title,
        'channel': info.channel,
        'channelId': info.channel_id,
        'duration': info.duration,
        'uploadDate': info.upload_date,
        'tags': info.tags or [],
        'categories': info.categories or [],
        'description': info.description,
        'thumbnail': info.thumbnail_url or f'https://i.ytimg.com/vi/{info.video_id}/hqdefault.jpg',
        'chapters': [
            {'start': chapter.get('start_time'), 'title': chapter.get('title')}
            for chapter in info.chapters
        ] if info.chapters else None,
        'fetchedAt': info.epoch,
        'webpageUrl': info.webpage_url,
        'exported': info.exported,
    }


def collect_entries(state: BotState) -> dict[str, dict]:
    # Each video is stored under two keys (user query + canonical URL) that
    # share one VideoInfo, so entries are deduplicated by YouTube id. Rows
    # without video_id predate metadata enrichment and are skipped.
    entries = {}
    for info in state.videos.values():
        if not info.video_id or not info.file_id:
            continue
        entries.setdefault(info.video_id, video_entry(info))
    return entries


def parse_bool(value: str) -> bool:
    return value.lower() in ('1', 'true', 'yes')


async def api_videos(request: web.Request) -> web.Response:
    state = get_state(request)
    items = list(collect_entries(state).values())
    exported = request.query.get('exported')
    if exported is not None:
        want = parse_bool(exported)
        items = [item for item in items if item['exported'] == want]
    items.sort(key=lambda item: (item['uploadDate'] or '', item['id']), reverse=True)
    body = json.dumps(items, ensure_ascii=False)
    etag = f'"{hashlib.sha256(body.encode()).hexdigest()[:32]}"'
    if request.headers.get('If-None-Match') == etag:
        return web.Response(status=304, headers={'ETag': etag})
    return web.Response(text=body, content_type='application/json', headers={'ETag': etag})


async def api_video(request: web.Request) -> web.Response:
    state = get_state(request)
    entry = collect_entries(state).get(request.match_info['video_id'])
    if not entry:
        return web.json_response({'error': 'not found'}, status=404)
    return web.json_response(entry)
