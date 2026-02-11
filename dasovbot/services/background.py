from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING

from telegram import Bot

from dasovbot.constants import SOURCE_SUBSCRIPTION
from dasovbot.downloader import extract_info, extract_url, filter_entries, get_ydl
from dasovbot.helpers import now
from dasovbot.services.intent_processor import append_intent, post_process

if TYPE_CHECKING:
    from dasovbot.state import BotState

logger = logging.getLogger(__name__)


async def populate_files(state: BotState):
    from dasovbot.constants import INTERVAL_SEC
    while True:
        await asyncio.sleep(INTERVAL_SEC)
        state.save()
        state.background_task_status['populate_files'] = now()


async def populate_subscriptions(state: BotState):
    from dasovbot.constants import INTERVAL_SEC
    while True:
        for url in list(state.subscriptions.keys()):
            subscription = state.subscriptions.get(url)
            if not subscription:
                continue
            chat_ids = subscription.chat_ids
            if chat_ids:
                await populate_playlist(url, chat_ids, state)
            else:
                state.subscriptions.pop(url, None)
        state.background_task_status['populate_subscriptions'] = now()
        await asyncio.sleep(INTERVAL_SEC)


async def populate_playlist(channel: str, chat_ids: list, state: BotState):
    ydl = get_ydl()
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, partial(ydl.extract_info, channel, download=False))
    except Exception:
        logger.error("populate_playlist error: %s", channel, exc_info=True)
        return
    entries = info.get('entries')
    if not entries:
        logger.info("populate_playlist no entries: %s", channel)
        return
    for entry in filter_entries(entries)[:5][::-1]:
        await populate_video(extract_url(entry), chat_ids, state, title=entry.get('title'), upload_date=entry.get('upload_date'))


async def populate_video(query: str, chat_ids: list, state: BotState, title: str = None, upload_date: str = None):
    info = state.videos.get(query)
    file_id = info.file_id if info else None
    if file_id:
        return info
    await append_intent(query, state, chat_ids=chat_ids, source=SOURCE_SUBSCRIPTION, title=title, upload_date=upload_date)


async def populate_animation(bot: Bot, state: BotState):
    if state.animation_file_id:
        logger.info("saved_animation_file_id = %s", state.animation_file_id)
        return

    query = state.config.loading_video_id

    info = await extract_info(query, download=True, state=state)

    message = await bot.send_video(
        chat_id=state.config.developer_chat_id,
        video=info.filepath,
        filename=info.filename,
        duration=info.duration,
        width=info.width,
        height=info.height,
        caption=info.caption,
        disable_notification=True,
    )

    state.animation_file_id = await post_process(query, info, message, state, store_info=False)
    logger.info("animation_file_id = %s", state.animation_file_id)


async def clear_temporary_inline_queries(state: BotState):
    while True:
        for url in list(state.temporary_inline_queries.keys()):
            tiq = state.temporary_inline_queries.get(url)
            if not tiq:
                continue
            if tiq.marked:
                del state.temporary_inline_queries[url]
            else:
                tiq.marked = True
        state.background_task_status['clear_temporary_inline_queries'] = now()
        await asyncio.sleep(10 * 60)


def start_background_tasks(bot: Bot, state: BotState):
    from dasovbot.dashboard.server import start_dashboard
    from dasovbot.services.intent_processor import monitor_process_intents
    asyncio.gather(
        populate_animation(bot, state),
        populate_subscriptions(state),
        populate_files(state),
        monitor_process_intents(bot, state),
        clear_temporary_inline_queries(state),
        start_dashboard(state),
    )
