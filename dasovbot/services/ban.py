from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from telegram import Bot, Message

from dasovbot.constants import BANNED_FAILURE_DELAY_SEC, SOURCE_DOWNLOAD
from dasovbot.helpers import extract_user

if TYPE_CHECKING:
    from dasovbot.state import BotState

logger = logging.getLogger(__name__)


def unavailable_caption(query: str) -> str:
    # Same wording notify_intent_failed uses for dead videos
    return f'❌ Video unavailable\n{query}'


async def _fail_later(bot: Bot, query: str, delay: float, **target):
    await asyncio.sleep(delay)
    try:
        await bot.edit_message_caption(caption=unavailable_caption(query), **target)
    except Exception:
        logger.warning("banned failure edit error: %s", query, exc_info=True)


def schedule_fake_failure(bot: Bot, state: BotState, query: str, **target):
    """Turn a banned user's loading placeholder into a failure after a random delay.

    ``target`` addresses the placeholder for ``edit_message_caption``:
    ``inline_message_id=...`` or ``chat_id=..., message_id=...``. Runs as a
    task so the handler returns at once instead of holding the update.
    """
    delay = random.uniform(*BANNED_FAILURE_DELAY_SEC)
    task = asyncio.create_task(_fail_later(bot, query, delay, **target))
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)


async def fake_download(bot: Bot, state: BotState, message: Message, query: str):
    """Answer a banned user's download request like a dead video.

    Shows the loading animation, then fails it after the usual delay. Without
    an animation nothing is sent, which is also what an ordinary failed
    download looks like then: requesters without a placeholder are never
    messaged (see notify_intent_failed). The attempt is logged like any other.
    """
    user = message.from_user
    logger.info("%s # download banned: %s", extract_user(user), query)
    await state.record_request(user.id, query, SOURCE_DOWNLOAD)
    if not state.animation_file_id:
        return
    try:
        placeholder = await message.reply_video(
            video=state.animation_file_id,
            caption=query,
            reply_to_message_id=message.id,
        )
        schedule_fake_failure(bot, state, query, chat_id=str(message.chat_id), message_id=placeholder.message_id)
    except Exception as e:
        logger.error("%s # download banned error: %s", extract_user(user), query, exc_info=e)
