import logging

from telegram import Update, ReplyKeyboardRemove

from dasovbot.helpers import extract_user

logger = logging.getLogger(__name__)


async def start(update: Update, _):
    message = update.message
    username = message.from_user['username']
    await message.reply_text(f"Hey, @{username}.\n"
                             "Welcome to Download and Share Online Video bot\n"
                             "Type /download\n\n"
                             "/help - for more details")


async def help_command(update: Update, _):
    await update.message.reply_markdown(
        "@dasovbot - Download and share video\n\n"
        "/download - Download video\n\n"
        "*Subscriptions*\n"
        "/subscriptions - Show list of subscriptions\n"
        "/subscribe - Subscribe to playlist\n"
        "/unsubscribe - Unsubscribe from playlist"
    )


async def unknown(update: Update, _):
    await update.message.reply_text(
        "Unknown command. Please type /help for available commands"
    )


async def cancel(update: Update, _) -> int:
    from telegram.ext import ConversationHandler
    message = update.message
    logger.info("%s # cancel", extract_user(message.from_user))
    await message.reply_text("Cancelled", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END
