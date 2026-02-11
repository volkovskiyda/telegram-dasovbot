import logging

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ConversationHandler

from dasovbot.constants import DAS_URL, SOURCE_DOWNLOAD
from dasovbot.downloader import extract_info
from dasovbot.helpers import extract_user, remove_command_prefix
from dasovbot.state import BotState
from dasovbot.services.intent_processor import append_intent

logger = logging.getLogger(__name__)


async def download(update: Update, _) -> int:
    message = update.message
    if remove_command_prefix(message.text):
        return await download_url(update, _)
    else:
        await message.reply_text("Enter url")
        return DAS_URL


async def download_url(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    message = update.message
    user = message.from_user
    chat_id = str(message.chat_id)
    query = remove_command_prefix(message.text)

    logger.info("%s # download_url: %s", extract_user(user), query)

    if not query:
        return ConversationHandler.END

    info = await extract_info(query, download=False, state=state)
    if not info or info.entries:
        await message.reply_text("Unsupported url", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    try:
        video = await message.reply_video(
            video=state.animation_file_id,
            caption=info.caption,
            reply_to_message_id=message.id,
        )
        await append_intent(query, state, message={'chat': chat_id, 'message': str(video.message_id)}, source=SOURCE_DOWNLOAD)
    except Exception as e:
        logger.error("%s # download_url error: %s", extract_user(user), query, exc_info=e)

    state.users[chat_id] = user.to_dict()
    return ConversationHandler.END
