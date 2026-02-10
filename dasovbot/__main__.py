import asyncio
import logging
from warnings import filterwarnings

from telegram import Update
from telegram.ext import Application
from telegram.warnings import PTBUserWarning

from dasovbot.config import load_config
from dasovbot.downloader import init_downloader
from dasovbot.handlers import register_handlers
from dasovbot.services.background import start_background_tasks
from dasovbot.state import BotState


def main():
    logging.basicConfig(
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
        level=logging.INFO,
    )
    filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

    config = load_config()
    init_downloader(config)
    state = BotState.from_files(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = (
        Application.builder()
        .token(config.bot_token)
        .base_url(config.base_url)
        .read_timeout(config.read_timeout)
        .build()
    )

    application.bot_data['state'] = state

    register_handlers(application)

    start_background_tasks(application.bot, state)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
