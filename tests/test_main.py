import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dasovbot.__main__ import main, build_application
from tests.helpers import make_config


def make_builder(app):
    builder = MagicMock()
    for method in ('token', 'base_url', 'base_file_url', 'read_timeout', 'local_mode'):
        getattr(builder, method).return_value = builder
    builder.build.return_value = app
    return builder


@patch('dasovbot.__main__.run_application', new_callable=AsyncMock)
@patch('dasovbot.dashboard.server.start_dashboard', new_callable=AsyncMock)
@patch('dasovbot.__main__.register_handlers')
@patch('dasovbot.__main__.Application')
@patch('dasovbot.__main__.BotState')
@patch('dasovbot.__main__.init_downloader')
@patch('dasovbot.__main__.load_config')
class TestMain(unittest.TestCase):
    def tearDown(self):
        # main() creates and sets an event loop but never closes it
        loop = asyncio.get_event_loop_policy().get_event_loop()
        loop.close()
        asyncio.set_event_loop(None)

    def _make_state(self):
        state = MagicMock()
        state.migrate_and_load = AsyncMock()
        state.close = AsyncMock()
        state.db = AsyncMock()
        return state

    def test_wires_app_under_the_role_controller(self, mock_load, mock_init, mock_state_cls,
                                                 mock_app_cls, mock_register, mock_dashboard, mock_run):
        from dasovbot.services.ha import RoleController, PtbRunner
        config = make_config()
        mock_load.return_value = config
        state = self._make_state()
        mock_state_cls.create = AsyncMock(return_value=state)
        app = MagicMock()
        app.bot_data = {}
        mock_app_cls.builder.return_value = make_builder(app)

        main()

        mock_init.assert_called_once_with(config)
        mock_dashboard.assert_awaited_once()
        dashboard_state, controller = mock_dashboard.await_args.args
        self.assertIs(dashboard_state, state)
        self.assertIsInstance(controller, RoleController)
        state.migrate_and_load.assert_awaited_once()
        self.assertIs(app.bot_data['state'], state)
        self.assertIs(app.bot_data['ha'], controller)
        mock_register.assert_called_once_with(app)
        self.assertIsInstance(controller.runner, PtbRunner)
        self.assertIs(controller.runner.app, app)
        self.assertIsNone(controller.peer, 'standalone: no sync client')
        mock_run.assert_awaited_once()
        run_app, run_ctl, run_state, run_sync = mock_run.await_args.args
        self.assertIs(run_app, app)
        self.assertIs(run_ctl, controller)
        self.assertIsNone(run_sync)
        app.run_polling.assert_not_called()

        import logging
        httpx_logger = logging.getLogger('httpx')
        self.addCleanup(httpx_logger.filters.clear)
        make_record = lambda msg: logging.LogRecord('httpx', logging.INFO, __file__, 1, msg, None, None)
        self.assertFalse(httpx_logger.filter(make_record('HTTP Request: POST /bot/getUpdates')))
        self.assertTrue(httpx_logger.filter(make_record('HTTP Request: POST /bot/sendMessage')))

    def test_ha_pair_gets_a_sync_client(self, mock_load, mock_init, mock_state_cls,
                                        mock_app_cls, mock_register, mock_dashboard, mock_run):
        from dasovbot.services.sync import SyncClient
        mock_load.return_value = make_config(peer_url='http://192.168.11.7:8080', sync_secret='s')
        state = self._make_state()
        mock_state_cls.create = AsyncMock(return_value=state)
        app = MagicMock()
        app.bot_data = {}
        mock_app_cls.builder.return_value = make_builder(app)

        with patch.object(SyncClient, 'load', new_callable=AsyncMock) as mock_sync_load:
            main()

        mock_sync_load.assert_awaited_once()
        _, controller = mock_dashboard.await_args.args
        self.assertIsInstance(controller.peer, SyncClient)
        self.assertIs(mock_run.await_args.args[3], controller.peer)

    def test_state_init_failure_aborts(self, mock_load, mock_init, mock_state_cls,
                                       mock_app_cls, mock_register, mock_dashboard, mock_run):
        mock_load.return_value = make_config()
        mock_state_cls.create = AsyncMock(side_effect=RuntimeError('boom'))

        main()

        mock_dashboard.assert_not_awaited()
        mock_app_cls.builder.assert_not_called()
        mock_run.assert_not_awaited()

    def test_migration_failure_aborts(self, mock_load, mock_init, mock_state_cls,
                                      mock_app_cls, mock_register, mock_dashboard, mock_run):
        mock_load.return_value = make_config()
        state = self._make_state()
        state.migrate_and_load.side_effect = RuntimeError('boom')
        mock_state_cls.create = AsyncMock(return_value=state)

        main()

        mock_dashboard.assert_awaited_once()
        mock_app_cls.builder.assert_not_called()
        mock_run.assert_not_awaited()
        # The DB must be closed on abort: aiosqlite's worker thread is
        # non-daemon, so a leaked connection keeps the dead process alive
        state.close.assert_awaited_once()


class TestRunApplication(unittest.IsolatedAsyncioTestCase):
    def _app(self, updater_running=True, running=True):
        app = MagicMock()
        for name in ('initialize', 'start', 'stop', 'shutdown'):
            setattr(app, name, AsyncMock())
        app.updater.stop = AsyncMock()
        app.updater.running = updater_running
        app.running = running
        return app

    def _state(self):
        state = MagicMock()
        state.close = AsyncMock()
        return state

    @patch('dasovbot.services.background.stop_background_tasks', new_callable=AsyncMock)
    async def test_lifecycle_order_and_shutdown(self, mock_stop_tasks):
        from dasovbot.__main__ import run_application
        app, state = self._app(), self._state()
        controller = MagicMock()
        controller.start = AsyncMock()
        controller.stop = AsyncMock()
        sync_client = MagicMock()
        sync_client.aclose = AsyncMock()
        order = []
        for owner, name in ((app, 'initialize'), (app, 'start'), (controller, 'start'), (controller, 'stop'),
                            (app.updater, 'stop'), (app, 'stop'), (app, 'shutdown'), (state, 'close')):
            getattr(owner, name).side_effect = (lambda label: (lambda *a, **k: order.append(label)))(
                f'{"ctl" if owner is controller else "updater" if owner is app.updater else "state" if owner is state else "app"}.{name}')
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, stop.set)

        await run_application(app, controller, state, sync_client, stop=stop, install_signal_handlers=False)

        self.assertEqual(order, ['app.initialize', 'app.start', 'ctl.start', 'ctl.stop', 'updater.stop',
                                 'app.stop', 'app.shutdown', 'state.close'])
        mock_stop_tasks.assert_awaited_once_with(state)
        sync_client.aclose.assert_awaited_once()
        app.run_polling.assert_not_called()

    @patch('dasovbot.services.background.stop_background_tasks', new_callable=AsyncMock)
    async def test_shutdown_skips_updater_and_app_when_not_running(self, mock_stop_tasks):
        from dasovbot.__main__ import run_application
        app, state = self._app(updater_running=False, running=False), self._state()
        controller = MagicMock()
        controller.start = AsyncMock()
        controller.stop = AsyncMock()
        stop = asyncio.Event()
        stop.set()

        await run_application(app, controller, state, None, stop=stop, install_signal_handlers=False)

        app.updater.stop.assert_not_awaited()
        app.stop.assert_not_awaited()
        app.shutdown.assert_awaited_once()
        state.close.assert_awaited_once()

    @patch('dasovbot.services.background.stop_background_tasks', new_callable=AsyncMock)
    async def test_controller_start_failure_still_cleans_up(self, mock_stop_tasks):
        from dasovbot.__main__ import run_application
        app, state = self._app(), self._state()
        controller = MagicMock()
        controller.start = AsyncMock(side_effect=RuntimeError('no peer config'))
        controller.stop = AsyncMock()
        with self.assertRaises(RuntimeError):
            await run_application(app, controller, state, None, stop=asyncio.Event(),
                                  install_signal_handlers=False)
        app.shutdown.assert_awaited_once()
        state.close.assert_awaited_once()


class TestBuildApplication(unittest.TestCase):
    """Builds a real (offline) PTB Application to pin the bot wiring.

    local_mode is what keeps multi-GB uploads out of process memory: without
    it PTB reads the whole file into an InputFile before POSTing it.
    """

    def _build(self, **overrides):
        config = make_config(bot_token='123:abc', **overrides)
        return build_application(config)

    def test_enables_local_mode_when_configured(self):
        app = self._build(
            base_url='http://localhost:8081/bot',
            base_file_url='http://localhost:8081/file/bot',
            local_mode=True,
        )
        self.assertTrue(app.bot.local_mode)
        self.assertEqual(app.bot.base_url, 'http://localhost:8081/bot123:abc')
        self.assertEqual(app.bot.base_file_url, 'http://localhost:8081/file/bot123:abc')

    def test_local_mode_off_by_default(self):
        app = self._build(base_url='http://localhost:8081/bot')
        self.assertFalse(app.bot.local_mode)

    def test_empty_base_file_url_keeps_library_default(self):
        app = self._build()
        self.assertFalse(app.bot.local_mode)
        self.assertIn('api.telegram.org/file/bot', app.bot.base_file_url)

    def test_no_lifecycle_hooks_registered(self):
        # Polling and background tasks are owned by the role controller now
        app = self._build()
        self.assertIsNone(app.post_init)
        self.assertIsNone(app.post_shutdown)


if __name__ == '__main__':
    unittest.main()
