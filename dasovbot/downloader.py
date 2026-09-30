from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

import yt_dlp

from dasovbot.config import Config, make_ydl_opts
from dasovbot.constants import DATETIME_FORMAT, TIMEOUT_SEC, VIDEO_ERROR_MESSAGES
from dasovbot.models import VideoInfo
from dasovbot.persistence import remove

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


# Path -> the attempt that most recently touched it. A retry of a YouTube
# video renders the same output path as the attempt it replaces, so a late
# cleanup from the abandoned attempt must not delete the retry's files.
_path_owners: dict[str, DownloadAttempt] = {}
_path_owners_lock = threading.Lock()


class DownloadAttempt:
    """One yt-dlp download: cancellable, and removes its files unless it succeeds.

    The executor thread running yt-dlp cannot be killed, so a timed-out
    download used to keep running and leave a finished (never sent) file in
    the media folder, once per retry. cancel() makes the next progress or
    postprocessor callback raise DownloadCancelled; the thread then deletes
    everything the attempt wrote when it ends. A download that had already
    finished when cancel() arrived is deleted as well: its caller is
    discarding the result.
    """

    def __init__(self):
        self._cancelled = threading.Event()
        self._paths: set[str] = set()
        # Orders cancel() (event loop thread) against finish() (executor
        # thread): the two cross when wait_for times out in the same instant
        # the download succeeds
        self._lock = threading.Lock()
        # Paths a successful finish() kept for the caller
        self._kept: list[str] = []

    def cancel(self):
        with self._lock:
            self._cancelled.set()
            kept, self._kept = self._kept, []
        if not kept:
            return
        # Succeeded just before the timeout: the caller never sees the
        # result, so nothing would ever send or remove the file
        with _path_owners_lock:
            for path in kept:
                if path not in _path_owners:
                    _remove_download_files(path)
        logger.info("download attempt cancelled after success, removed %d path(s): %s", len(kept), sorted(kept))

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def attach(self, ydl: yt_dlp.YoutubeDL):
        ydl.add_progress_hook(self._progress_hook)
        ydl.add_postprocessor_hook(self._postprocessor_hook)

    def _claim(self, path: str | None):
        if not path:
            return
        with _path_owners_lock:
            self._paths.add(path)
            _path_owners[path] = self

    def _check_cancelled(self):
        if self._cancelled.is_set():
            raise yt_dlp.utils.DownloadCancelled('download attempt cancelled')

    def _progress_hook(self, status: dict):
        self._claim(status.get('tmpfilename'))
        self._claim(status.get('filename'))
        self._check_cancelled()

    def _postprocessor_hook(self, status: dict):
        self._claim((status.get('info_dict') or {}).get('filepath'))
        self._check_cancelled()

    def finish(self, succeeded: bool):
        """Called in the executor thread once yt-dlp returns or raises.

        A download that completed after cancel() is discarded too: its caller
        already gave up on it and nothing else will ever send or remove it.
        """
        with self._lock:
            discard = not succeeded or self.cancelled
            with _path_owners_lock:
                owned = [path for path in self._paths if _path_owners.get(path) is self]
                for path in owned:
                    del _path_owners[path]
                if discard:
                    for path in owned:
                        _remove_download_files(path)
            if not discard:
                self._kept = owned
        if discard and owned:
            logger.info("download attempt cleaned up %d path(s): %s", len(owned), sorted(owned))


def _remove_download_files(path: str):
    remove(path)
    # Fragmented (HLS/DASH) downloads keep per-fragment files and a resume
    # state file next to the .part until they are assembled
    remove(f'{path}.ytdl')
    for fragment in glob.glob(f'{glob.escape(path)}-Frag*'):
        remove(fragment)


def _run_ydl(ydl: yt_dlp.YoutubeDL, query: str, download: bool, attempt: DownloadAttempt | None):
    # Blocking: use and close the YoutubeDL entirely inside the calling
    # (executor) thread, so an abandoned wait_for timeout still releases its
    # network resources when the thread eventually finishes.
    if attempt:
        attempt.attach(ydl)
    succeeded = False
    try:
        result = ydl.extract_info(query, download=download)
        succeeded = True
        return result
    finally:
        ydl.close()
        if attempt:
            attempt.finish(succeeded)


def extract_info_sync(query: str, download: bool = False, attempt: DownloadAttempt | None = None):
    return _run_ydl(get_ydl(), query, download, attempt)


def _extract_info_opts_sync(opts: dict, query: str, attempt: DownloadAttempt):
    # Like extract_info_sync, with caller-supplied opts (the 360p fallback)
    return _run_ydl(yt_dlp.YoutubeDL(dict(opts)), query, True, attempt)


async def _run_download(func, attempt: DownloadAttempt):
    """Run a blocking yt-dlp download bounded by TIMEOUT_SEC.

    On timeout (or task cancellation) the attempt is cancelled so yt-dlp stops
    at its next callback and cleans up, instead of finishing in the background.
    """
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, func)
    try:
        return await asyncio.wait_for(future, TIMEOUT_SEC)
    except BaseException:
        # Harmless when the thread itself raised: it already finished
        attempt.cancel()
        raise


async def download_with_opts(opts: dict, query: str):
    """Serialized, time-bounded download with custom opts (the 360p fallback).

    Acquires the same lock as the primary download path so yt-dlp downloads
    never overlap, and bounds the wait like the primary path does. Raises
    asyncio.TimeoutError when the bound is exceeded.
    """
    async with _lock:
        attempt = DownloadAttempt()
        return await _run_download(partial(_extract_info_opts_sync, opts, query, attempt), attempt)


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
    description = info.get('description') or ''
    info_title = info.get('title')
    title = info_title or url
    caption_title = info_title[:100] if info_title else ''
    date_prefix = f"[{upload_date}] " if upload_date else ''
    caption = f"{date_prefix}{caption_title}\n{url}"

    raw_chapters = info.get('chapters')
    chapters = [
        {'start_time': chapter.get('start_time'), 'title': chapter.get('title')}
        for chapter in raw_chapters
    ] if raw_chapters else None

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
        video_id=id,
        channel=info.get('channel') or info.get('uploader'),
        channel_id=info.get('channel_id') or info.get('uploader_id'),
        tags=info.get('tags'),
        categories=info.get('categories'),
        chapters=chapters,
        thumbnail_url=info.get('thumbnail'),
        epoch=int(info.get('epoch') or time.time()),
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
                attempt = DownloadAttempt()
                raw_info = await _run_download(
                    partial(extract_info_sync, query, download=True, attempt=attempt), attempt)
                logger.info("extract_info downloaded: %s", query)
                info = process_info(raw_info)
        except asyncio.TimeoutError:
            # The attempt was cancelled, but yt-dlp only stops at its next
            # callback (a merge or a stalled socket delays that). Hold the
            # intent back for a full timeout window so a retry does not write
            # the same output path concurrently with that thread.
            state.intent_retry_after[query] = time.monotonic() + TIMEOUT_SEC
            logger.warning("extract_info timeout, download cancelled: %s", query)
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
        ok = result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except (subprocess.TimeoutExpired, OSError):
        ok = False
    if not ok and os.path.exists(output_path):
        # Any failed run (nonzero exit included) must not leave a partial
        # .mp4 accumulating in the media folder
        os.remove(output_path)
    return ok


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

    if await loop.run_in_executor(None, _run_ffmpeg, filepath, output_path, ['-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac']):
        logger.info("convert_to_mp4 transcoded: %s", filepath)
        _cleanup_original(filepath, output_path)
        return output_path

    logger.warning("convert_to_mp4 failed, using original: %s", filepath)
    return filepath
