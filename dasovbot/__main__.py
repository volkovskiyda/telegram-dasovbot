import asyncio
import logging
from warnings import filterwarnings

from telegram import Update
from telegram.ext import Application
from telegram.warnings import PTBUserWarning

from dasovbot.config import Config, load_config
from dasovbot.downloader import init_downloader
from dasovbot.handlers import register_handlers
from dasovbot.state import BotState


def build_application(config: Config, post_init, post_shutdown) -> Application:
    # local_mode hands uploads to the Bot API server as file:// paths it reads
    # straight from disk — a multi-GB video must never be loaded into this
    # process. Requires a server started with --local (see docker-compose.yml)
    # that can reach the media folder under the same absolute path.
    builder = (
        Application.builder()
        .token(config.bot_token)
        .base_url(config.base_url)
        .read_timeout(config.read_timeout)
        .local_mode(config.local_mode)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    if config.base_file_url:
        builder = builder.base_file_url(config.base_file_url)
    return builder.build()


def main():
    logging.basicConfig(
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
        level=logging.INFO,
    )

    # Suppress noisy logs
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    class _IgnoreGetUpdates(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "getUpdates" not in record.getMessage()

    logging.getLogger("httpx").addFilter(_IgnoreGetUpdates())

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

    async def post_shutdown(app: Application):
        from dasovbot.services.background import stop_background_tasks
        shutdown_state = app.bot_data['state']
        await stop_background_tasks(shutdown_state)
        await shutdown_state.close()

    application = build_application(config, post_init, post_shutdown)

    application.bot_data['state'] = state

    register_handlers(application)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
