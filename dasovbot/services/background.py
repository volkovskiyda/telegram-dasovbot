from __future__ import annotations

import asyncio
import glob
import logging
import os
import time
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

# The dashboard can trigger a run while the hourly loop is mid-iteration;
# both mutate state.subscriptions and enqueue intents, so runs must not overlap
_populate_lock = asyncio.Lock()


async def run_populate_subscriptions(state: BotState):
    if _populate_lock.locked():
        logger.info("populate_subscriptions already running, skipped")
        return
    async with _populate_lock:
        for url in list(state.subscriptions.keys()):
            subscription = state.subscriptions.get(url)
            if not subscription:
                continue
            chat_ids = subscription.chat_ids
            if chat_ids:
                await populate_playlist(url, chat_ids, state)
            else:
                await state.pop_subscription(url)
        state.background_task_status['populate_subscriptions'] = now()


async def populate_subscriptions(state: BotState):
    from dasovbot.constants import INTERVAL_SEC
    while True:
        await run_populate_subscriptions(state)
        await asyncio.sleep(INTERVAL_SEC)


def _playlist_info_sync(channel: str) -> dict:
    # Create, use and close the YoutubeDL inside the executor thread:
    # un-closed instances permanently retain HTTP sessions and SSL contexts.
    ydl = get_ydl()
    try:
        return ydl.extract_info(channel, download=False)
    finally:
        ydl.close()


async def populate_playlist(channel: str, chat_ids: list, state: BotState):
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, partial(_playlist_info_sync, channel))
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
    from dasovbot.constants import RESTART_DELAY_SEC

    if state.animation_file_id:
        logger.info("saved_animation_file_id = %s", state.animation_file_id)
        return

    query = state.config.loading_video_id
    if not query:
        logger.warning("populate_animation skipped: LOADING_VIDEO_ID not set")
        return

    for attempt in range(1, 6):
        try:
            info = await extract_info(query, download=True, state=state)
            if not info or not info.filepath:
                logger.warning("populate_animation no info, attempt %s: %s", attempt, query)
            else:
                async with state.upload_semaphore:
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
                if state.animation_file_id:
                    return
        except Exception:
            logger.error("populate_animation failed, attempt %s: %s", attempt, query, exc_info=True)
        await asyncio.sleep(RESTART_DELAY_SEC)
    logger.error("populate_animation gave up: %s", query)


async def clear_temporary_inline_queries(state: BotState):
    while True:
        for url in list(state.temporary_inline_queries.keys()):
            tiq = state.temporary_inline_queries.get(url)
            if not tiq:
                continue
            # Keep ignored entries: they stop repeated yt-dlp hits for dead
            # videos and stay visible on the dashboard until handled there
            if tiq.ignored:
                continue
            if tiq.marked:
                del state.temporary_inline_queries[url]
            else:
                tiq.marked = True
        state.background_task_status['clear_temporary_inline_queries'] = now()
        await asyncio.sleep(10 * 60)


def newest_backup_age(backup_dir: str) -> float | None:
    """Seconds since the most recent bot.db.backup_*, or None if there are none."""
    backups = glob.glob(os.path.join(backup_dir, 'bot.db.backup_*'))
    newest = max((os.path.getmtime(path) for path in backups), default=None)
    return None if newest is None else time.time() - newest


async def monitor_backups(bot: Bot, state: BotState):
    """Alert the developer when the backup job appears to have stopped.

    Covers every failure mode at once — cron not running, the backup script
    erroring, or a misrouted path producing no fresh files — by watching the
    age of the newest backup rather than the job itself. Alerts once per stale
    episode and re-arms when a fresh backup reappears.
    """
    from dasovbot.constants import BACKUP_CHECK_INTERVAL_SEC, BACKUP_STALE_SEC

    backup_dir = os.path.dirname(state.config.db_file) or '.'
    alerted = False
    while True:
        try:
            age = newest_backup_age(backup_dir)
            stale = age is None or age > BACKUP_STALE_SEC
            if stale:
                if age is None:
                    text = f"No database backups found in {backup_dir}. The backup job may not be running."
                else:
                    text = (f"Latest database backup is {age / 3600:.1f}h old "
                            f"(alert threshold {BACKUP_STALE_SEC // 3600}h). Backups may have stopped.")
                state.set_alert('backup_stale', text, level='warning')
                if not alerted:
                    try:
                        await bot.send_message(
                            chat_id=state.config.developer_chat_id, text=f"⚠️ {text}", disable_notification=True)
                        logger.warning(text)
                        alerted = True  # only latch after a successful send, so a failed alert retries
                    except Exception:
                        logger.error("monitor_backups: failed to alert developer", exc_info=True)
            else:
                state.clear_alert('backup_stale')
                alerted = False
            state.background_task_status['monitor_backups'] = now()
        except Exception:
            logger.error("monitor_backups error", exc_info=True)
        await asyncio.sleep(BACKUP_CHECK_INTERVAL_SEC)


async def run_forever(factory, name: str):
    from dasovbot.constants import RESTART_DELAY_SEC
    while True:
        try:
            await factory()
            return
        except Exception:
            logger.error("background task %s crashed, restarting", name, exc_info=True)
        await asyncio.sleep(RESTART_DELAY_SEC)


def _on_task_done(state: BotState, task: asyncio.Task):
    state.background_tasks.discard(task)
    if not task.cancelled() and task.exception():
        logger.error("Background task %s failed: %s", task.get_name(), task.exception(), exc_info=task.exception())


def start_background_tasks(bot: Bot, state: BotState):
    from dasovbot.services.intent_processor import monitor_process_intents

    # monitor_process_intents restarts itself; populate_animation is finite;
    # the two infinite loops get the same crash-restart treatment via run_forever
    tasks = [
        asyncio.create_task(populate_animation(bot, state), name="populate_animation"),
        asyncio.create_task(
            run_forever(partial(populate_subscriptions, state), 'populate_subscriptions'),
            name="populate_subscriptions"),
        asyncio.create_task(monitor_process_intents(bot, state), name="monitor_process_intents"),
        asyncio.create_task(
            run_forever(partial(clear_temporary_inline_queries, state), 'clear_temporary_inline_queries'),
            name="clear_temporary_inline_queries"),
        asyncio.create_task(
            run_forever(partial(monitor_backups, bot, state), 'monitor_backups'),
            name="monitor_backups"),
    ]
    for task in tasks:
        state.background_tasks.add(task)
        task.add_done_callback(partial(_on_task_done, state))


async def stop_background_tasks(state: BotState):
    tasks = list(state.background_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
