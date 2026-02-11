import unittest
from unittest.mock import patch, AsyncMock, MagicMock

import dasovbot.dashboard.auth as auth_module
from dasovbot.dashboard.auth import get_password, make_token, check_token


class TestGetPassword(unittest.TestCase):

    def setUp(self):
        auth_module._generated_password = None

    @patch.dict('os.environ', {'DASHBOARD_PASSWORD': 'my_secret'})
    def test_returns_env_password(self):
        self.assertEqual(get_password(), 'my_secret')

    @patch.dict('os.environ', {}, clear=True)
    def test_generates_password_when_env_missing(self):
        password = get_password()
        self.assertIsInstance(password, str)
        self.assertGreater(len(password), 0)

    @patch.dict('os.environ', {}, clear=True)
    def test_generated_password_is_stable(self):
        first = get_password()
        second = get_password()
        self.assertEqual(first, second)

    @patch.dict('os.environ', {}, clear=True)
    def test_generated_password_differs_across_resets(self):
        first = get_password()
        auth_module._generated_password = None
        second = get_password()
        # Extremely unlikely to collide
        self.assertNotEqual(first, second)


class TestCheckToken(unittest.TestCase):

    def setUp(self):
        auth_module._generated_password = None

    @patch.dict('os.environ', {'DASHBOARD_PASSWORD': 'pass123'})
    def test_valid_token(self):
        token = make_token('pass123')
        request = MagicMock()
        request.cookies = {'dasovbot_token': token}
        self.assertTrue(check_token(request))

    @patch.dict('os.environ', {'DASHBOARD_PASSWORD': 'pass123'})
    def test_invalid_token(self):
        request = MagicMock()
        request.cookies = {'dasovbot_token': 'wrong'}
        self.assertFalse(check_token(request))

    @patch.dict('os.environ', {}, clear=True)
    def test_works_with_generated_password(self):
        password = get_password()
        token = make_token(password)
        request = MagicMock()
        request.cookies = {'dasovbot_token': token}
        self.assertTrue(check_token(request))


class TestStartDashboard(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        auth_module._generated_password = None

    @patch('dasovbot.dashboard.server.web.TCPSite')
    @patch('dasovbot.dashboard.server.web.AppRunner')
    @patch('dasovbot.dashboard.server.create_app')
    @patch.dict('os.environ', {}, clear=True)
    async def test_logs_generated_password(self, mock_create_app, mock_runner_cls, mock_site_cls):
        runner = AsyncMock()
        mock_runner_cls.return_value = runner
        site = AsyncMock()
        mock_site_cls.return_value = site

        from dasovbot.dashboard.server import start_dashboard
        state = MagicMock()

        with self.assertLogs('dasovbot.dashboard.server', level='INFO') as cm:
            await start_dashboard(state)

        log_output = '\n'.join(cm.output)
        self.assertIn('generated password:', log_output)
        site.start.assert_awaited_once()

    @patch('dasovbot.dashboard.server.web.TCPSite')
    @patch('dasovbot.dashboard.server.web.AppRunner')
    @patch('dasovbot.dashboard.server.create_app')
    @patch.dict('os.environ', {'DASHBOARD_PASSWORD': 'explicit'})
    async def test_no_log_when_env_set(self, mock_create_app, mock_runner_cls, mock_site_cls):
        runner = AsyncMock()
        mock_runner_cls.return_value = runner
        site = AsyncMock()
        mock_site_cls.return_value = site

        from dasovbot.dashboard.server import start_dashboard
        state = MagicMock()

        with self.assertLogs('dasovbot.dashboard.server', level='INFO') as cm:
            await start_dashboard(state)

        log_output = '\n'.join(cm.output)
        self.assertNotIn('generated password:', log_output)
        self.assertIn('Dashboard started', log_output)
