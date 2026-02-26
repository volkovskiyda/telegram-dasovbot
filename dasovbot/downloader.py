from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

import yt_dlp

from dasovbot.config import Config, make_ydl_opts
from dasovbot.constants import DATETIME_FORMAT, TIMEOUT_SEC, VIDEO_ERROR_MESSAGES
from dasovbot.models import VideoInfo

if TYPE_CHECKING:
    from dasovbot.state import BotState

logger = logging.getLogger(__name__)

_ydl: yt_dlp.YoutubeDL | None = None
_lock = asyncio.Lock()


def init_downloader(config: Config):
    global _ydl
    ydl_opts = make_ydl_opts(config)
    _ydl = yt_dlp.YoutubeDL(ydl_opts)


def get_ydl() -> yt_dlp.YoutubeDL:
    return _ydl


def get_ydl_opts() -> dict:
    return make_ydl_opts.__wrapped__() if hasattr(make_ydl_opts, '__wrapped__') else _ydl.params


def extract_url(info) -> str:
    if isinstance(info, VideoInfo):
        return info.webpage_url or info.url
    return info.get('webpage_url') or info['url']


def process_info(info) -> VideoInfo | None:
    if not info:
        return None
    if isinstance(info, VideoInfo):
        return info

    requested_downloads_list = info.get('requested_downloads')
    if requested_downloads_list:
        requested_downloads = requested_downloads_list[0]
        filepath = requested_downloads['filepath']
        filename = requested_downloads['filename']
    else:
        filepath = None
        filename = None

    url = extract_url(info)
    id = info.get('id')
    if id:
        thumbnail = f"https://i.ytimg.com/vi/{id}/default.jpg"
    else:
        thumbnail = info.get('thumbnail')

    timestamp = info.get('timestamp')
    if timestamp:
        timestamp = datetime.fromtimestamp(timestamp).strftime(DATETIME_FORMAT)

    upload_date = info.get('upload_date')
    info_description = info.get('description')
    description = info_description[:1000] if info_description else ''
    info_title = info.get('title')
    title = info_title or url
    caption_title = info_title[:100] if info_title else ''
    caption = f"[{upload_date}] {caption_title}\n{url}"

    return VideoInfo(
        file_id=info.get('file_id'),
        webpage_url=info.get('webpage_url'),
        title=title,
        description=description,
        upload_date=upload_date,
        timestamp=timestamp,
        thumbnail=thumbnail,
        duration=int(info.get('duration') or 0),
        uploader_url=info.get('uploader_url'),
        width=info.get('width'),
        height=info.get('height'),
        caption=caption,
        url=info.get('url'),
        filepath=filepath,
        filename=filename,
        format=info.get('format'),
        entries=info.get('entries'),
    )


def contains_text(origin: str, text: list[str]) -> bool:
    for item in text:
        if item.lower() in origin.lower():
            return True
    return False


def process_entries(entries: list) -> list:
    nested_entries = entries[0].get('entries')
    return nested_entries if nested_entries else filter_entries(entries)


def filter_entries(entries: list) -> list:
    return list(filter(
        lambda entry: entry.get('duration') and
        (entry.get('live_status') is None or entry['live_status'] != 'is_live') and
        (entry.get('availability') is None or entry['availability'] != 'subscriber_only'),
        entries
    ))


def add_scaled_after_title(value: str | dict) -> str | dict:
    if isinstance(value, dict):
        return {k: add_scaled_after_title(v) for k, v in value.items()}
    elif isinstance(value, str):
        return re.sub(r'(%\(title\)(?:\.\d+)?s)(?!\.scaled\b)', r'\1.scaled', value)
    return value


async def extract_info(query: str, download: bool, state: BotState) -> VideoInfo | None:
    info = state.videos.get(query)
    if info and (info.file_id or not download):
        return info

    if not info:
        try:
            loop = asyncio.get_running_loop()
            raw_info = await loop.run_in_executor(None, partial(_ydl.extract_info, query, download=False))
            url = extract_url(raw_info)
            info_url = state.videos.get(url)
            if info_url:
                await state.set_video(query, info_url)
                return info_url
            info = process_info(raw_info)
        except Exception as e:
            if isinstance(e, yt_dlp.DownloadError) and contains_text(e.msg, VIDEO_ERROR_MESSAGES):
                intent = state.intents.get(query) or state.temporary_inline_queries.get(query)
                if intent:
                    intent.ignored = True
                return None
            logger.error("extract_info error: %s", query)

    needs_download = download and (not info or not info.file_id)
    if needs_download:
        try:
            async with _lock:
                logger.debug("lock_acquire")
                loop = asyncio.get_running_loop()
                future = loop.run_in_executor(None, partial(_ydl.extract_info, query, download=True))
                raw_info = await asyncio.wait_for(future, TIMEOUT_SEC)
                logger.info("extract_info downloaded: %s", query)
                info = process_info(raw_info)
        except asyncio.TimeoutError:
            logger.warning("extract_info timeout: %s", query)
        except Exception as e:
            logger.error("extract_info download error: %s", query, exc_info=e)
        finally:
            logger.debug("lock_release")

    return info
