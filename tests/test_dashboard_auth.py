import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

import dasovbot.dashboard.auth as auth_module
from dasovbot.dashboard.auth import (
    auth_middleware, login_page, login_post, logout,
    create_session, check_token, client_ip, secure_cookie,
    COOKIE_NAME, MAX_LOGIN_ATTEMPTS,
)


def make_request(remote='1.2.3.4', forwarded=None, proto=None):
    request = MagicMock()
    request.remote = remote
    request.headers = {}
    if forwarded:
        request.headers['X-Forwarded-For'] = forwarded
    if proto:
        request.headers['X-Forwarded-Proto'] = proto
    return request


class AuthTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        auth_module._generated_password = None
        auth_module._sessions.clear()
        auth_module._failed_logins.clear()


class TestCreateSession(AuthTestCase):
    def test_login_sweeps_expired_sessions(self):
        auth_module._sessions['old'] = time.time() - 1
        auth_module._sessions['live'] = time.time() + 100
        token = create_session()
        self.assertNotIn('old', auth_module._sessions)
        self.assertIn('live', auth_module._sessions)
        self.assertGreater(auth_module._sessions[token], time.time())


class TestClientIp(AuthTestCase):
    def test_ignores_forwarded_header_by_default(self):
        request = make_request(remote='10.0.0.2', forwarded='6.6.6.6')
        self.assertEqual(client_ip(request), '10.0.0.2')

    @patch.dict('os.environ', {'DASHBOARD_BEHIND_PROXY': 'true'})
    def test_uses_rightmost_forwarded_entry_behind_proxy(self):
        # Leftmost entries are client-supplied; only the last one comes from our proxy
        request = make_request(remote='10.0.0.2', forwarded='6.6.6.6, 203.0.113.7')
        self.assertEqual(client_ip(request), '203.0.113.7')

    @patch.dict('os.environ', {'DASHBOARD_BEHIND_PROXY': 'true'})
    def test_falls_back_to_remote_without_header(self):
        request = make_request(remote='10.0.0.2')
        self.assertEqual(client_ip(request), '10.0.0.2')

    @patch.dict('os.environ', {'DASHBOARD_BEHIND_PROXY': 'true'})
    @patch('dasovbot.dashboard.auth.get_password', return_value='secret')
    async def test_rate_limit_isolated_per_forwarded_client(self, mock_pwd):
        # Same proxy remote, different real clients: one client's failures
        # must not lock out the other
        attacker = make_request(remote='10.0.0.2', forwarded='203.0.113.7')
        attacker.post = AsyncMock(return_value={'password': 'wrong'})
        for _ in range(MAX_LOGIN_ATTEMPTS):
            with self.assertRaises(web.HTTPFound):
                await login_post(attacker)

        victim = make_request(remote='10.0.0.2', forwarded='198.51.100.9')
        victim.post = AsyncMock(return_value={'password': 'secret'})
        with self.assertRaises(web.HTTPFound) as ctx:
            await login_post(victim)
        self.assertEqual(ctx.exception.location, '/')


class TestSecureCookie(AuthTestCase):
    def test_plain_deployment_not_secure(self):
        self.assertFalse(secure_cookie(make_request(proto='https')))

    @patch.dict('os.environ', {'DASHBOARD_BEHIND_PROXY': 'true'})
    def test_behind_https_proxy_is_secure(self):
        self.assertTrue(secure_cookie(make_request(proto='https')))

    @patch.dict('os.environ', {'DASHBOARD_BEHIND_PROXY': 'true'})
    def test_behind_plain_http_proxy_not_secure(self):
        # A Secure cookie would be dropped by the browser over plain HTTP
        self.assertFalse(secure_cookie(make_request(proto='http')))
        self.assertFalse(secure_cookie(make_request()))

    @patch.dict('os.environ', {'DASHBOARD_BEHIND_PROXY': 'true'})
    @patch('dasovbot.dashboard.auth.get_password', return_value='secret')
    async def test_login_sets_secure_cookie_behind_https_proxy(self, mock_pwd):
        request = make_request(forwarded='203.0.113.7', proto='https')
        request.post = AsyncMock(return_value={'password': 'secret'})
        with self.assertRaises(web.HTTPFound) as ctx:
            await login_post(request)
        self.assertTrue(ctx.exception.cookies[COOKIE_NAME]['secure'])


class TestAuthMiddleware(AuthTestCase):
    async def test_login_path_passes_through(self):
        request = MagicMock()
        request.path = '/login'
        handler = AsyncMock(return_value=web.Response(text='ok'))
        result = await auth_middleware(request, handler)
        handler.assert_awaited_once_with(request)
        self.assertEqual(result.text, 'ok')

    async def test_valid_session_passes(self):
        token = create_session()
        request = MagicMock()
        request.path = '/'
        request.cookies = {COOKIE_NAME: token}
        handler = AsyncMock(return_value=web.Response(text='ok'))
        await auth_middleware(request, handler)
        handler.assert_awaited_once()

    async def test_invalid_token_redirects(self):
        request = MagicMock()
        request.path = '/'
        request.cookies = {COOKIE_NAME: 'wrong-token'}
        handler = AsyncMock()
        with self.assertRaises(web.HTTPFound) as ctx:
            await auth_middleware(request, handler)
        self.assertEqual(ctx.exception.location, '/login')
        handler.assert_not_awaited()

    async def test_no_cookie_redirects(self):
        request = MagicMock()
        request.path = '/'
        request.cookies = {}
        handler = AsyncMock()
        with self.assertRaises(web.HTTPFound):
            await auth_middleware(request, handler)

    async def test_expired_session_redirects(self):
        token = create_session()
        auth_module._sessions[token] = time.time() - 1
        request = MagicMock()
        request.path = '/'
        request.cookies = {COOKIE_NAME: token}
        handler = AsyncMock()
        with self.assertRaises(web.HTTPFound):
            await auth_middleware(request, handler)
        self.assertNotIn(token, auth_module._sessions)


class TestLoginPage(AuthTestCase):
    @patch('dasovbot.dashboard.auth.check_token', return_value=True)
    async def test_redirects_if_authenticated(self, mock_check):
        request = MagicMock()
        with self.assertRaises(web.HTTPFound) as ctx:
            await login_page(request)
        self.assertEqual(ctx.exception.location, '/')

    @patch('aiohttp_jinja2.render_template')
    @patch('dasovbot.dashboard.auth.check_token', return_value=False)
    async def test_renders_if_not_authenticated(self, mock_check, mock_render):
        request = MagicMock()
        request.query = {}
        mock_render.return_value = web.Response(text='login page')
        result = await login_page(request)
        mock_render.assert_called_once()
        self.assertEqual(mock_render.call_args[0][0], 'login.html')

    @patch('aiohttp_jinja2.render_template')
    @patch('dasovbot.dashboard.auth.check_token', return_value=False)
    async def test_passes_error_param(self, mock_check, mock_render):
        request = MagicMock()
        request.query = {'error': '1'}
        mock_render.return_value = web.Response(text='login')
        await login_page(request)
        context = mock_render.call_args[0][2]
        self.assertEqual(context['error'], '1')


class TestLoginPost(AuthTestCase):
    @patch('dasovbot.dashboard.auth.get_password', return_value='secret')
    async def test_correct_password_sets_session_cookie_and_redirects(self, mock_pwd):
        request = MagicMock()
        request.remote = '1.2.3.4'
        request.post = AsyncMock(return_value={'password': 'secret'})
        with self.assertRaises(web.HTTPFound) as ctx:
            await login_post(request)
        self.assertEqual(ctx.exception.location, '/')
        cookies = ctx.exception.cookies
        self.assertIn(COOKIE_NAME, {m.key for m in cookies.values()})
        token = cookies[COOKIE_NAME].value
        self.assertIn(token, auth_module._sessions)

    @patch('dasovbot.dashboard.auth.get_password', return_value='secret')
    async def test_wrong_password_redirects_with_error(self, mock_pwd):
        request = MagicMock()
        request.remote = '1.2.3.4'
        request.post = AsyncMock(return_value={'password': 'wrong'})
        with self.assertRaises(web.HTTPFound) as ctx:
            await login_post(request)
        self.assertIn('error', ctx.exception.location)

    @patch('dasovbot.dashboard.auth.get_password', return_value='secret')
    async def test_rate_limits_after_repeated_failures(self, mock_pwd):
        request = MagicMock()
        request.remote = '1.2.3.4'
        request.post = AsyncMock(return_value={'password': 'wrong'})
        for _ in range(MAX_LOGIN_ATTEMPTS):
            with self.assertRaises(web.HTTPFound):
                await login_post(request)
        # Even the correct password is rejected while rate limited
        request.post = AsyncMock(return_value={'password': 'secret'})
        with self.assertRaises(web.HTTPFound) as ctx:
            await login_post(request)
        self.assertEqual(ctx.exception.location, '/login?error=2')

    @patch('dasovbot.dashboard.auth.get_password', return_value='secret')
    async def test_each_login_creates_unique_session(self, mock_pwd):
        tokens = set()
        for _ in range(2):
            request = MagicMock()
            request.remote = '1.2.3.4'
            request.post = AsyncMock(return_value={'password': 'secret'})
            with self.assertRaises(web.HTTPFound) as ctx:
                await login_post(request)
            tokens.add(ctx.exception.cookies[COOKIE_NAME].value)
        self.assertEqual(len(tokens), 2)


class TestLogout(AuthTestCase):
    async def test_deletes_session_and_redirects(self):
        token = create_session()
        request = MagicMock()
        request.cookies = {COOKIE_NAME: token}
        with self.assertRaises(web.HTTPFound) as ctx:
            await logout(request)
        self.assertEqual(ctx.exception.location, '/login')
        self.assertNotIn(token, auth_module._sessions)


if __name__ == '__main__':
    unittest.main()
