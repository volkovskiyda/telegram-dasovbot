from __future__ import annotations

import asyncio
import logging
import os
import shutil
from functools import partial
from typing import TYPE_CHECKING

import yt_dlp
from telegram import Bot, InputMediaVideo, Message
from telegram.error import NetworkError

from dasovbot.config import make_ydl_opts
from dasovbot.downloader import (
    extract_info, extract_url, process_info,
    add_scaled_after_title, convert_to_mp4,
)
from dasovbot.helpers import send_message_developer, now
from dasovbot.models import VideoInfo, VideoOrigin, Intent, IntentMessage
from dasovbot.persistence import remove

if TYPE_CHECKING:
    from dasovbot.state import BotState

logger = logging.getLogger(__name__)


def filter_intents(intents: dict) -> dict:
    return {query: intent for query, intent in intents.items() if not intent.ignored}


def file_size_mb(path: str) -> int:
    """Size of ``path`` in whole megabytes, or 0 if it can't be read.

    Called from an except handler, so a missing/removed file must not raise a
    second exception that escapes into the crash-restart path.
    """
    try:
        return os.path.getsize(path) >> 20
    except OSError:
        return 0


async def drop_or_retry_intent(query: str, state: BotState):
    from dasovbot.constants import MAX_INTENT_RETRIES
    intent = state.intents.get(query)
    if not intent or intent.ignored:
        return
    intent.retries += 1
    if intent.retries >= MAX_INTENT_RETRIES:
        logger.warning("intent dropped after %d failed attempts: %s", intent.retries, query)
        await state.pop_intent(query)
    else:
        await state.save_intent(query)


async def append_intent(query: str, state: BotState, chat_ids=None, inline_message_id: str = '', message=None, source: str = None, title: str = None, upload_date: str = None):
    if chat_ids is None:
        chat_ids = []
    if message is None:
        message = {}

    intent = state.intents.get(query)
    is_new = not intent
    if is_new:
        intent = Intent()

    if source and intent.source is None:
        intent.source = source
    if title and intent.title is None:
        intent.title = title
    if upload_date and intent.upload_date is None:
        intent.upload_date = upload_date

    for item in chat_ids:
        if item not in intent.chat_ids:
            intent.chat_ids.append(item)
    if inline_message_id:
        intent.inline_message_ids.append(inline_message_id)
    if message:
        intent.messages.append(IntentMessage.from_dict(message))
    if not intent.ignored:
        intent.priority += len(chat_ids) or 2

    if is_new:
        await state.set_intent(query, intent)
    else:
        await state.save_intent(query)
    state.download_queue.put_nowait(query)


async def post_process(query: str, info: VideoInfo, message: Message, state: BotState, store_info=True, origin_info: VideoInfo = None) -> str | None:
    # Telegram may return the upload as a document/animation instead of a video
    attachment = message.video or message.document or message.animation
    file_id = attachment.file_id if attachment else None
    try:
        await message.delete()
    except Exception:
        pass
    filepath = info.filepath
    if not file_id:
        logger.error("post_process missing file_id: %s", query)
        remove(filepath)
        return None
    info.file_id = file_id
    if store_info:
        url = extract_url(info)
        intent = state.intents.get(query)
        if intent and intent.source:
            info.source = intent.source
        info.processed_at = now()
        info.url = None
        info.filepath = None
        info.filename = None
        info.entries = None
        if origin_info:
            info.origin = VideoOrigin(
                width=origin_info.width,
                height=origin_info.height,
                format=origin_info.format,
            )
        await state.set_video(query, info)
        await state.set_video(url, info)
    if filepath:
        chat_ids = []
        intent = state.intents.get(query)
        if intent:
            chat_ids = intent.chat_ids or [m.chat for m in intent.messages]
        developer_id = state.config.developer_id
        if (developer_id in chat_ids or str(message.chat_id) == developer_id) and '/media/' in filepath:
            export_path = '/export/'.join(filepath.rsplit('/media/', 1))
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, shutil.move, filepath, export_path)
            except Exception:
                logger.error("move_file error: %s", query, exc_info=True)
                remove(filepath)
        else:
            # A path outside the media folder cannot be mapped to /export/;
            # moving it onto itself would silently leak the file on disk
            remove(filepath)
    return file_id


async def process_intents(bot: Bot, state: BotState):
    from dasovbot.constants import PROCESS_INTERVAL_SEC
    while True:
        state.background_task_status['monitor_process_intents'] = now()
        # The queue is only a wake-up signal: drain accumulated puts so it stays bounded
        while not state.download_queue.empty():
            state.download_queue.get_nowait()
        filtered_intents = filter_intents(state.intents)
        if not filtered_intents:
            # Nothing to do: block until a new request signals the queue, then
            # loop back and re-scan immediately (no throttle on the idle path).
            await state.download_queue.get()
            continue
        max_priority = max(filtered_intents, key=lambda key: filtered_intents[key].priority)
        await process_query(bot, max_priority, state)
        await asyncio.sleep(PROCESS_INTERVAL_SEC)


async def monitor_process_intents(bot: Bot, state: BotState):
    from dasovbot.constants import RESTART_DELAY_SEC
    from dasovbot.persistence import empty_media_folder_files
    while True:
        try:
            await process_intents(bot, state)
        except Exception as e:
            logger.error("process_intents crashed: %s, %s", type(e).__name__, str(e), exc_info=e)
            if state.config.empty_media_folder:
                empty_media_folder_files(state.config.media_folder)
        await send_message_developer(bot, '[error_monitor_process_intents]', state.config.developer_id)
        await asyncio.sleep(RESTART_DELAY_SEC)


async def process_query(bot: Bot, query: str, state: BotState) -> VideoInfo:
    config = state.config
    info = await extract_info(query, download=True, state=state)
    if not info:
        logger.error("process_query error (no info): %s", query)
        await drop_or_retry_intent(query, state)
        return info
    caption = info.caption
    file_id = info.file_id
    logger.info("process_query info: %s file_id=%s filepath=%s", query, file_id, info.filepath)
    if not file_id:
        try:
            video_path = info.filepath
            if not video_path:
                logger.error("process_query no video path: %s", query)
                # extract_url may be None for dead videos with no url fields
                if 'youtube' in (extract_url(info) or ''):
                    await send_message_developer(bot, f'[error_no_video_path]\n{caption}', config.developer_id)
                await drop_or_retry_intent(query, state)
                return info
            video_path = await convert_to_mp4(video_path)
            if video_path != info.filepath:
                info.filepath = video_path
                info.filename = os.path.splitext(info.filename)[0] + '.mp4' if info.filename else None
            # Released before the except path runs, so the fallback in
            # retry_lower_quality can re-acquire without deadlocking
            async with state.upload_semaphore:
                logger.info("process_query send_video strt: %s", query)
                message = await bot.send_video(
                    chat_id=config.developer_chat_id,
                    caption=caption,
                    video=video_path,
                    duration=info.duration,
                    width=info.width,
                    height=info.height,
                    filename=info.filename,
                    disable_notification=True,
                )
            logger.info("process_query send_video fnsh: %s file_id=%s", query, message.video.file_id if message.video else None)
        except Exception as e:
            logger.error("process_query send_video error: %s %s: %s", query, type(e).__name__, e)
            from dasovbot.constants import LARGE_FILE_MB
            if isinstance(e, NetworkError) and video_path and file_size_mb(video_path) > LARGE_FILE_MB and 'youtube' in (extract_url(info) or ''):
                if await retry_lower_quality(bot, query, info, state):
                    return info
            remove(info.filepath)
            await drop_or_retry_intent(query, state)
            return info
        file_id = await post_process(query, info, message, state)
        logger.info("process_query post_process done: %s file_id=%s", query, file_id)
        if not file_id:
            await drop_or_retry_intent(query, state)
            return info

    await process_intent(bot, query, file_id, caption, state)
    return info


async def retry_lower_quality(bot: Bot, query: str, info: VideoInfo, state: BotState) -> bool:
    """Re-download a too-large video at 360p and post it. Returns True when posted."""
    from dasovbot.constants import FALLBACK_VIDEO_RES
    config = state.config
    caption = info.caption
    await send_message_developer(bot, f'[error_large_video]\n{caption}', config.developer_id)
    temp_ydl_opts = make_ydl_opts(config)
    # Override the sort, not the format string: the resolution no longer
    # appears in the format itself, so a textual substitution would be a
    # silent no-op and re-download the same too-large rendition.
    temp_ydl_opts['format_sort'] = [f'res:{FALLBACK_VIDEO_RES}']
    temp_ydl_opts['outtmpl'] = add_scaled_after_title(temp_ydl_opts['outtmpl'])
    temp_video_path = None
    temp_info = None
    loop = asyncio.get_running_loop()
    with yt_dlp.YoutubeDL(temp_ydl_opts) as temp_ydl:
        try:
            temp_info_raw = await loop.run_in_executor(
                None, partial(temp_ydl.extract_info, query, download=True)
            )
            temp_info = process_info(temp_info_raw)
            temp_video_path = temp_info.filepath
        except Exception:
            logger.error("fallback download error: %s", query, exc_info=True)
    if not temp_info or not temp_video_path:
        return False
    temp_video_path = await convert_to_mp4(temp_video_path)
    if temp_video_path != temp_info.filepath:
        temp_info.filepath = temp_video_path
    try:
        async with state.upload_semaphore:
            logger.info("process_query send_video rsrt: %s", query)
            message = await bot.send_video(
                chat_id=config.developer_chat_id,
                caption=caption,
                video=temp_video_path,
                duration=info.duration,
                width=temp_info.width or info.width,
                height=temp_info.height or info.height,
                filename=info.filename,
                disable_notification=True,
            )
        logger.info("process_query send_video fnsh: %s", query)
        await send_message_developer(bot, f'[error_fixed_large_video]\n{caption}', config.developer_id, notification=False)
        file_id = await post_process(query, info, message, state, origin_info=temp_info)
        if not file_id:
            return False
        await process_intent(bot, query, file_id, caption, state)
        return True
    except Exception:
        logger.error("fallback send_video error: %s", query, exc_info=True)
        return False
    finally:
        logger.info("process_query remove: %s", temp_video_path)
        if temp_video_path:
            remove(temp_video_path)


async def _deliver(send, kind: str, query: str, target) -> bool:
    """Run a single Telegram delivery, retrying when rate-limited (RetryAfter).

    The intent is already popped by the time we deliver, so a throttled send
    that is merely logged loses that recipient's video permanently. Honour the
    server's retry_after a bounded number of times before giving up.
    """
    from telegram.error import RetryAfter
    from dasovbot.constants import MAX_SEND_RETRIES
    for attempt in range(MAX_SEND_RETRIES + 1):
        try:
            await send()
            return True
        except RetryAfter as e:
            if attempt >= MAX_SEND_RETRIES:
                logger.error("process_intent %s rate-limited, gave up: %s - %s", kind, query, target)
                return False
            logger.warning("process_intent %s rate-limited, retry in %ss: %s - %s", kind, e.retry_after, query, target)
            await asyncio.sleep(e.retry_after)
        except Exception:
            logger.error("process_intent %s error: %s - %s", kind, query, target, exc_info=True)
            return False
    return False


async def process_intent(bot: Bot, query: str, video: str, caption: str, state: BotState) -> Intent | None:
    intent = await state.pop_intent(query)
    if not intent:
        logger.warning("process_intent no intent found: %s", query)
        return None
    logger.info("process_intent: %s chat_ids=%s inline=%d messages=%d", query, intent.chat_ids, len(intent.inline_message_ids), len(intent.messages))
    for item in intent.chat_ids:
        await _deliver(lambda item=item: bot.send_video(chat_id=item, video=video, caption=caption, disable_notification=True), 'chat_ids', query, item)
    for item in intent.inline_message_ids:
        await _deliver(lambda item=item: bot.edit_message_media(inline_message_id=item, media=InputMediaVideo(media=video, caption=caption)), 'inline_message_ids', query, item)
    for item in intent.messages:
        await _deliver(lambda item=item: bot.edit_message_media(chat_id=item.chat, message_id=item.message, media=InputMediaVideo(media=video, caption=caption)), 'messages', query, item)
    return intent
