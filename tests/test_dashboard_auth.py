import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from dasovbot.dashboard.auth import (
    auth_middleware, login_page, login_post, logout,
    make_token, get_password, COOKIE_NAME,
)


class TestAuthMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_login_path_passes_through(self):
        request = MagicMock()
        request.path = '/login'
        handler = AsyncMock(return_value=web.Response(text='ok'))
        result = await auth_middleware(request, handler)
        handler.assert_awaited_once_with(request)
        self.assertEqual(result.text, 'ok')

    @patch('dasovbot.dashboard.auth.get_password', return_value='testpass')
    async def test_valid_token_passes(self, mock_pwd):
        token = make_token('testpass')
        request = MagicMock()
        request.path = '/'
        request.cookies = {COOKIE_NAME: token}
        handler = AsyncMock(return_value=web.Response(text='ok'))
        result = await auth_middleware(request, handler)
        handler.assert_awaited_once()

    @patch('dasovbot.dashboard.auth.get_password', return_value='testpass')
    async def test_invalid_token_redirects(self, mock_pwd):
        request = MagicMock()
        request.path = '/'
        request.cookies = {COOKIE_NAME: 'wrong-token'}
        handler = AsyncMock()
        with self.assertRaises(web.HTTPFound) as ctx:
            await auth_middleware(request, handler)
        self.assertEqual(ctx.exception.location, '/login')
        handler.assert_not_awaited()

    @patch('dasovbot.dashboard.auth.get_password', return_value='testpass')
    async def test_no_cookie_redirects(self, mock_pwd):
        request = MagicMock()
        request.path = '/'
        request.cookies = {}
        handler = AsyncMock()
        with self.assertRaises(web.HTTPFound):
            await auth_middleware(request, handler)


class TestLoginPage(unittest.IsolatedAsyncioTestCase):
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


class TestLoginPost(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.dashboard.auth.get_password', return_value='secret')
    async def test_correct_password_sets_cookie_and_redirects(self, mock_pwd):
        request = MagicMock()
        request.post = AsyncMock(return_value={'password': 'secret'})
        with self.assertRaises(web.HTTPFound) as ctx:
            await login_post(request)
        self.assertEqual(ctx.exception.location, '/')
        cookies = ctx.exception.cookies
        self.assertIn(COOKIE_NAME, {m.key for m in cookies.values()})

    @patch('dasovbot.dashboard.auth.get_password', return_value='secret')
    async def test_wrong_password_redirects_with_error(self, mock_pwd):
        request = MagicMock()
        request.post = AsyncMock(return_value={'password': 'wrong'})
        with self.assertRaises(web.HTTPFound) as ctx:
            await login_post(request)
        self.assertIn('error', ctx.exception.location)


class TestLogout(unittest.IsolatedAsyncioTestCase):
    async def test_deletes_cookie_and_redirects(self):
        request = MagicMock()
        with self.assertRaises(web.HTTPFound) as ctx:
            await logout(request)
        self.assertEqual(ctx.exception.location, '/login')


if __name__ == '__main__':
    unittest.main()
