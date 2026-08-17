from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
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

_ydl_opts: dict | None = None
_lock = asyncio.Lock()


def init_downloader(config: Config):
    global _ydl_opts
    _ydl_opts = make_ydl_opts(config)


def get_ydl() -> yt_dlp.YoutubeDL:
    # YoutubeDL is not thread-safe: each caller gets its own instance so
    # concurrent executor threads never share extraction state. Opts are
    # copied because YoutubeDL mutates the dict it is given.
    # Callers MUST close() the instance (prefer extract_info_sync): un-closed
    # instances permanently retain HTTP sessions and SSL contexts (~1-2.5 MB
    # each), which leaked ~170 MB/h in production.
    # Caveat: with a cookiefile configured, every close() rewrites the jar and
    # concurrent extractions would race on that file (plain truncate-and-write,
    # no locking) — serialize load/save before enabling COOKIES_FILE.
    return yt_dlp.YoutubeDL(dict(_ydl_opts))


def extract_info_sync(query: str, download: bool = False):
    # Blocking: create, use and close the YoutubeDL entirely inside the
    # calling (executor) thread, so an abandoned wait_for timeout still
    # releases its network resources when the thread eventually finishes.
    ydl = get_ydl()
    try:
        return ydl.extract_info(query, download=download)
    finally:
        ydl.close()


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
    date_prefix = f"[{upload_date}] " if upload_date else ''
    caption = f"{date_prefix}{caption_title}\n{url}"

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
            future = loop.run_in_executor(None, partial(extract_info_sync, query, download=False))
            raw_info = await asyncio.wait_for(future, TIMEOUT_SEC)
            url = extract_url(raw_info)
            info_url = state.videos.get(url)
            if info_url:
                await state.set_video(query, info_url)
                return info_url
            info = process_info(raw_info)
        except asyncio.TimeoutError:
            logger.warning("extract_info metadata timeout: %s", query)
            return None
        except Exception as e:
            if isinstance(e, yt_dlp.DownloadError) and contains_text(e.msg, VIDEO_ERROR_MESSAGES):
                intent = state.intents.get(query)
                if intent:
                    intent.ignored = True
                    await state.save_intent(query)
                else:
                    tiq = state.temporary_inline_queries.get(query)
                    if tiq:
                        tiq.ignored = True
                return None
            logger.error("extract_info error: %s", query)
            return None

    needs_download = download and (not info or not info.file_id)
    if needs_download:
        try:
            async with _lock:
                logger.debug("lock_acquire")
                loop = asyncio.get_running_loop()
                future = loop.run_in_executor(None, partial(extract_info_sync, query, download=True))
                raw_info = await asyncio.wait_for(future, TIMEOUT_SEC)
                logger.info("extract_info downloaded: %s", query)
                info = process_info(raw_info)
        except asyncio.TimeoutError:
            # The executor thread cannot be cancelled: yt-dlp may keep
            # downloading in the background after the lock is released. Hold
            # the intent back for a full timeout window so a retry does not
            # write the same output path concurrently with that thread.
            state.intent_retry_after[query] = time.monotonic() + TIMEOUT_SEC
            logger.warning("extract_info timeout, download may still be running: %s", query)
        except Exception as e:
            logger.error("extract_info download error: %s", query, exc_info=e)
        finally:
            logger.debug("lock_release")

    return info


def _run_ffmpeg(input_path: str, output_path: str, codec_args: list[str]) -> bool:
    try:
        # -nostats/-loglevel error: capture_output buffers everything ffmpeg
        # prints, and default progress stats accumulate for the whole encode.
        result = subprocess.run(
            ['ffmpeg', '-y', '-nostats', '-loglevel', 'error', '-i', input_path,
             *codec_args, '-movflags', '+faststart', output_path],
            capture_output=True, timeout=600,
        )
        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except (subprocess.TimeoutExpired, OSError):
        if os.path.exists(output_path):
            os.remove(output_path)
        return False


def _cleanup_original(original: str, new: str):
    try:
        os.remove(original)
    except OSError:
        logger.warning("cleanup_original failed: %s", original)


async def convert_to_mp4(filepath: str | None) -> str | None:
    if not filepath or filepath.lower().endswith('.mp4'):
        return filepath

    output_path = os.path.splitext(filepath)[0] + '.mp4'
    loop = asyncio.get_running_loop()

    if await loop.run_in_executor(None, _run_ffmpeg, filepath, output_path, ['-c', 'copy']):
        logger.info("convert_to_mp4 remuxed: %s", filepath)
        _cleanup_original(filepath, output_path)
        return output_path

    if os.path.exists(output_path):
        os.remove(output_path)

    if await loop.run_in_executor(None, _run_ffmpeg, filepath, output_path, ['-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac']):
        logger.info("convert_to_mp4 transcoded: %s", filepath)
        _cleanup_original(filepath, output_path)
        return output_path

    logger.warning("convert_to_mp4 failed, using original: %s", filepath)
    return filepath
