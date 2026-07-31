import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dasovbot.__main__ import main, build_application
from tests.helpers import make_config


def make_builder(app):
    builder = MagicMock()
    for method in ('token', 'base_url', 'base_file_url', 'read_timeout', 'local_mode',
                   'post_init', 'post_shutdown'):
        getattr(builder, method).return_value = builder
    builder.build.return_value = app
    return builder


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
        return state

    def test_wires_app_and_polls(self, mock_load, mock_init, mock_state_cls,
                                 mock_app_cls, mock_register, mock_dashboard):
        config = make_config()
        mock_load.return_value = config
        state = self._make_state()
        mock_state_cls.create = AsyncMock(return_value=state)
        app = MagicMock()
        app.bot_data = {}
        mock_app_cls.builder.return_value = make_builder(app)

        main()

        mock_init.assert_called_once_with(config)
        mock_dashboard.assert_awaited_once_with(state)
        state.migrate_and_load.assert_awaited_once()
        self.assertIs(app.bot_data['state'], state)
        mock_register.assert_called_once_with(app)
        app.run_polling.assert_called_once()

    @patch('dasovbot.services.background.stop_background_tasks', new_callable=AsyncMock)
    @patch('dasovbot.services.background.start_background_tasks')
    def test_post_init_and_shutdown_hooks(self, mock_start, mock_stop, mock_load, mock_init,
                                          mock_state_cls, mock_app_cls, mock_register, mock_dashboard):
        import logging
        mock_load.return_value = make_config()
        state = self._make_state()
        mock_state_cls.create = AsyncMock(return_value=state)
        app = MagicMock()
        app.bot_data = {}
        builder = make_builder(app)
        mock_app_cls.builder.return_value = builder

        main()

        loop = asyncio.get_event_loop_policy().get_event_loop()
        post_init_fn = builder.post_init.call_args.args[0]
        loop.run_until_complete(post_init_fn(app))
        mock_start.assert_called_once_with(app.bot, state)

        post_shutdown_fn = builder.post_shutdown.call_args.args[0]
        loop.run_until_complete(post_shutdown_fn(app))
        mock_stop.assert_awaited_once_with(state)
        state.close.assert_awaited_once()

        httpx_logger = logging.getLogger('httpx')
        self.addCleanup(httpx_logger.filters.clear)
        make_record = lambda msg: logging.LogRecord('httpx', logging.INFO, __file__, 1, msg, None, None)
        self.assertFalse(httpx_logger.filter(make_record('HTTP Request: POST /bot/getUpdates')))
        self.assertTrue(httpx_logger.filter(make_record('HTTP Request: POST /bot/sendMessage')))

    def test_state_init_failure_aborts(self, mock_load, mock_init, mock_state_cls,
                                       mock_app_cls, mock_register, mock_dashboard):
        mock_load.return_value = make_config()
        mock_state_cls.create = AsyncMock(side_effect=RuntimeError('boom'))

        main()

        mock_dashboard.assert_not_awaited()
        mock_app_cls.builder.assert_not_called()

    def test_migration_failure_aborts(self, mock_load, mock_init, mock_state_cls,
                                      mock_app_cls, mock_register, mock_dashboard):
        mock_load.return_value = make_config()
        state = self._make_state()
        state.migrate_and_load.side_effect = RuntimeError('boom')
        mock_state_cls.create = AsyncMock(return_value=state)

        main()

        mock_dashboard.assert_awaited_once_with(state)
        mock_app_cls.builder.assert_not_called()


class TestBuildApplication(unittest.TestCase):
    """Builds a real (offline) PTB Application to pin the bot wiring.

    local_mode is what keeps multi-GB uploads out of process memory: without
    it PTB reads the whole file into an InputFile before POSTing it.
    """

    def _build(self, **overrides):
        async def noop(app):
            pass

        config = make_config(bot_token='123:abc', **overrides)
        return build_application(config, noop, noop)

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


if __name__ == '__main__':
    unittest.main()
