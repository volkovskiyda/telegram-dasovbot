from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import TYPE_CHECKING

import yt_dlp
from telegram import Bot, InputMediaVideo, Message
from telegram.error import NetworkError

from dasovbot.config import make_ydl_opts
from dasovbot.downloader import (
    extract_info, extract_url, process_info,
    add_scaled_after_title,
)
from dasovbot.helpers import send_message_developer
from dasovbot.models import VideoInfo, VideoOrigin, Intent, IntentMessage
from dasovbot.persistence import remove

if TYPE_CHECKING:
    from dasovbot.state import BotState

logger = logging.getLogger(__name__)


def filter_intents(intents: dict) -> dict:
    return {query: intent for query, intent in intents.items() if not intent.ignored}


async def append_intent(query: str, state: BotState, chat_ids=None, inline_message_id: str = '', message=None):
    if chat_ids is None:
        chat_ids = []
    if message is None:
        message = {}

    intent = state.intents.get(query)
    if not intent:
        intent = Intent()
        state.intents[query] = intent

    for item in chat_ids:
        if item not in intent.chat_ids:
            intent.chat_ids.append(item)
    if inline_message_id:
        intent.inline_message_ids.append(inline_message_id)
    if message:
        intent.messages.append(IntentMessage.from_dict(message))
    if not intent.ignored:
        intent.priority += len(chat_ids) or 2
    state.download_queue.put_nowait(query)


async def post_process(query: str, info: VideoInfo, message: Message, state: BotState, store_info=True, origin_info: VideoInfo = None) -> str:
    file_id = message.video.file_id
    try:
        await message.delete()
    except:
        pass
    filepath = info.filepath
    info.file_id = file_id
    if store_info:
        url = extract_url(info)
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
        state.videos[query] = info
        state.videos[url] = info
    if filepath:
        chat_ids = []
        intent = state.intents.get(query)
        if intent:
            chat_ids = intent.chat_ids or [m.chat for m in intent.messages]
        developer_id = state.config.developer_id
        if developer_id in chat_ids or str(message.chat_id) == developer_id:
            try:
                shutil.move(filepath, '/export/'.join(filepath.rsplit('/media/', 1)))
            except:
                logger.error("move_file error: %s", query)
                remove(filepath)
        else:
            remove(filepath)
    return file_id


async def process_intents(bot: Bot, state: BotState):
    while True:
        await asyncio.sleep(10)
        filtered_intents = filter_intents(state.intents)
        if not filtered_intents:
            await state.download_queue.get()
        if not filtered_intents:
            continue
        max_priority = max(filtered_intents, key=lambda key: filtered_intents[key].priority)
        await process_query(bot, max_priority, state)


async def monitor_process_intents(bot: Bot, state: BotState):
    from dasovbot.constants import INTERVAL_SEC
    from dasovbot.persistence import empty_media_folder_files
    while True:
        try:
            await process_intents(bot, state)
        except Exception as e:
            logger.error("process_intents crashed: %s, %s", type(e).__name__, str(e), exc_info=e)
            if state.config.empty_media_folder:
                empty_media_folder_files(state.config.media_folder)
        await asyncio.sleep(INTERVAL_SEC)
        await send_message_developer(bot, '[error_monitor_process_intents]', state.config.developer_id)


async def process_query(bot: Bot, query: str, state: BotState) -> VideoInfo:
    config = state.config
    info = await extract_info(query, download=True, state=state)
    if not info:
        logger.error("process_query error: %s", query)
        if state.intents.get(query) and not state.intents[query].ignored:
            state.intents.pop(query, None)
        return info
    caption = info.caption
    file_id = info.file_id
    if not file_id:
        try:
            video_path = info.filepath
            if video_path:
                logger.info("process_query send_video strt: %s", query)
            elif 'youtube' in extract_url(info):
                await send_message_developer(bot, f'[error_no_video_path]\n{caption}', config.developer_id)
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
            logger.info("process_query send_video fnsh: %s", query)
        except Exception as e:
            if isinstance(e, NetworkError) and video_path and os.path.getsize(video_path) >> 20 > 2000 and 'youtube' in extract_url(info):
                await send_message_developer(bot, f'[error_large_video]\n{caption}', config.developer_id)
                temp_ydl_opts = make_ydl_opts(config)
                temp_ydl_opts['format'] = temp_ydl_opts['format'].replace('720', '360')
                temp_ydl_opts['outtmpl'] = add_scaled_after_title(temp_ydl_opts['outtmpl'])
                temp_video_path = None
                with yt_dlp.YoutubeDL(temp_ydl_opts) as temp_ydl:
                    try:
                        temp_info_raw = temp_ydl.extract_info(query, download=True)
                        temp_info = process_info(temp_info_raw)
                        temp_video_path = temp_info.filepath
                    except:
                        pass
                try:
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
                    await process_intent(bot, query, file_id, caption, state)
                    return info
                except:
                    pass
                finally:
                    logger.info("process_query remove: %s", temp_video_path)
                    if temp_video_path:
                        remove(temp_video_path)
            state.intents.pop(query, None)
            return info
        file_id = await post_process(query, info, message, state)

    await process_intent(bot, query, file_id, caption, state)
    return info


async def process_intent(bot: Bot, query: str, video: str, caption: str, state: BotState) -> Intent:
    intent = state.intents.pop(query)
    for item in intent.chat_ids:
        try:
            await bot.send_video(chat_id=item, video=video, caption=caption, disable_notification=True)
        except:
            logger.error("process_intent chat_ids error: %s - %s", query, item)
    for item in intent.inline_message_ids:
        try:
            await bot.edit_message_media(inline_message_id=item, media=InputMediaVideo(media=video, caption=caption))
        except:
            logger.error("process_intent inline_message_ids error: %s - %s", query, item)
    for item in intent.messages:
        try:
            await bot.edit_message_media(chat_id=item.chat, message_id=item.message, media=InputMediaVideo(media=video, caption=caption))
        except:
            logger.error("process_intent messages error: %s - %s", query, item)
    return intent
