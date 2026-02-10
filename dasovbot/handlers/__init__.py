from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, InlineQueryHandler,
    ChosenInlineResultHandler, ConversationHandler, CallbackQueryHandler, filters,
)

from dasovbot.constants import (
    DAS_URL, SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW,
    UNSUBSCRIBE_PLAYLIST, MULTIPLE_SUBSCRIBE_URLS,
)
from dasovbot.handlers.common import start, help_command, unknown, cancel
from dasovbot.handlers.download import download, download_url
from dasovbot.handlers.inline import inline_query_handler, chosen_query
from dasovbot.handlers.subscription import (
    subscription_list, subscribe, subscribe_url, subscribe_playlist,
    subscribe_show, unsubscribe, unsubscribe_playlist, playlists,
    multiple_subscribe, multiple_subscribe_urls,
)


def register_handlers(application: Application):
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))

    application.add_handler(InlineQueryHandler(inline_query_handler))
    application.add_handler(ChosenInlineResultHandler(chosen_query))
    application.add_handler(CommandHandler(['subscriptions', 'subs'], subscription_list))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['download', 'das', 'dv'], download)],
        states={
            DAS_URL: [MessageHandler(filters.TEXT, download_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['subscribe'], subscribe)],
        states={
            SUBSCRIBE_URL: [MessageHandler(filters.TEXT, subscribe_url)],
            SUBSCRIBE_PLAYLIST: [CallbackQueryHandler(subscribe_playlist)],
            SUBSCRIBE_SHOW: [CallbackQueryHandler(subscribe_show)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['unsubscribe'], unsubscribe)],
        states={
            UNSUBSCRIBE_PLAYLIST: [CallbackQueryHandler(unsubscribe_playlist)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(CommandHandler(['playlists'], playlists))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['multiple_subscribe'], multiple_subscribe)],
        states={
            MULTIPLE_SUBSCRIBE_URLS: [MessageHandler(filters.TEXT, multiple_subscribe_urls)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(CommandHandler(['multiple_subscribe'], multiple_subscribe))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
