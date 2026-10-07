import asyncio
import logging
import signal
from warnings import filterwarnings

from telegram.ext import Application
from telegram.warnings import PTBUserWarning

from dasovbot.config import Config, load_config
from dasovbot.downloader import init_downloader
from dasovbot.handlers import register_handlers
from dasovbot.state import BotState

logger = logging.getLogger(__name__)

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGABRT)


def build_application(config: Config) -> Application:
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
    )
    if config.base_file_url:
        builder = builder.base_file_url(config.base_file_url)
    return builder.build()


async def run_application(application: Application, controller, state: BotState, sync_client=None,
                          stop: asyncio.Event | None = None, install_signal_handlers: bool = True):
    """Drive the PTB lifecycle by hand so the role controller owns polling.

    run_polling() always starts the updater right after post_init, which a
    node that must start PASSIVE cannot accept. Order: initialize → start
    (update processing) → controller (polls only once ACTIVE) → wait for a
    stop signal → controller.stop → updater/application/shutdown → cleanup.
    """
    from dasovbot.services.background import stop_background_tasks

    stop = stop or asyncio.Event()
    if install_signal_handlers:
        loop = asyncio.get_running_loop()
        for sig in STOP_SIGNALS:
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError, ValueError) as exc:
                logger.warning("Could not add a signal handler for %s: %r", sig, exc)
    try:
        await application.initialize()
        await application.start()
        await controller.start()
        await stop.wait()
        logger.info("Stop signal received, shutting down")
    finally:
        await controller.stop()
        if application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        await application.shutdown()
        await stop_background_tasks(state)
        if sync_client is not None:
            await sync_client.aclose()
        await state.close()


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
    from dasovbot.services.ha import RoleController, PtbRunner
    from dasovbot.services.sync import SyncClient, DeveloperNotifier

    # The controller is built before the dashboard so the app can carry it
    # (aiohttp freezes the app once the runner is set up); its runner and the
    # notifier's bot are attached once the PTB application exists
    notifier = DeveloperNotifier(config)
    sync_client = SyncClient(config, state, notifier) if config.ha_enabled else None
    controller = RoleController(config, state, peer=sync_client, runner=None, notifier=notifier)
    loop.run_until_complete(start_dashboard(state, controller))

    try:
        loop.run_until_complete(state.migrate_and_load())
        if sync_client is not None:
            loop.run_until_complete(sync_client.load())
    except Exception as e:
        logging.error(f"Failed to migrate/load database: {e}")
        # Close the DB so aiosqlite's non-daemon worker thread exits; otherwise
        # the process lingers as a zombie (dashboard up, no bot) and Docker's
        # restart policy never fires
        loop.run_until_complete(state.close())
        return

    application = build_application(config)

    application.bot_data['state'] = state
    application.bot_data['ha'] = controller

    register_handlers(application)

    controller.attach_runner(PtbRunner(application, state))
    notifier.attach_bot(application.bot)

    loop.run_until_complete(run_application(application, controller, state, sync_client))


if __name__ == "__main__":
    main()
