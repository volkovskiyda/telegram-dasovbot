import asyncio
import logging
from warnings import filterwarnings

from telegram import Update
from telegram.ext import Application
from telegram.warnings import PTBUserWarning

from dasovbot.config import load_config
from dasovbot.downloader import init_downloader
from dasovbot.handlers import register_handlers
from dasovbot.state import BotState


def main():
    logging.basicConfig(
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
        level=logging.INFO,
    )
    filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

    config = load_config()
    init_downloader(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        state = loop.run_until_complete(BotState.create(config))
    except Exception as e:
        logging.error(f"Failed to initialize state: {e}")
        return

    from dasovbot.dashboard.server import start_dashboard
    loop.run_until_complete(start_dashboard(state))

    try:
        loop.run_until_complete(state.migrate_and_load())
    except Exception as e:
        logging.error(f"Failed to migrate/load database: {e}")
        return

    async def post_init(app: Application):
        from dasovbot.services.background import start_background_tasks
        start_background_tasks(app.bot, app.bot_data['state'])

    application = (
        Application.builder()
        .token(config.bot_token)
        .base_url(config.base_url)
        .read_timeout(config.read_timeout)
        .post_init(post_init)
        .build()
    )

    application.bot_data['state'] = state

    register_handlers(application)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
