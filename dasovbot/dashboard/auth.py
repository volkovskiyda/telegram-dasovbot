import hashlib
import os

from aiohttp import web


COOKIE_NAME = 'dasovbot_token'


def get_password() -> str | None:
    return os.getenv('DASHBOARD_PASSWORD')


def make_token(password: str) -> str:
    return hashlib.sha256(f'dasovbot:{password}'.encode()).hexdigest()


def check_token(request: web.Request) -> bool:
    password = get_password()
    if not password:
        return False
    token = request.cookies.get(COOKIE_NAME)
    return token == make_token(password)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path == '/login':
        return await handler(request)
    if not check_token(request):
        raise web.HTTPFound('/login')
    return await handler(request)


async def login_page(request: web.Request) -> web.Response:
    import aiohttp_jinja2
    if check_token(request):
        raise web.HTTPFound('/')
    error = request.query.get('error', '')
    return aiohttp_jinja2.render_template('login.html', request, {'error': error})


async def login_post(request: web.Request) -> web.Response:
    data = await request.post()
    password = data.get('password', '')
    expected = get_password()
    if password == expected:
        response = web.HTTPFound('/')
        response.set_cookie(COOKIE_NAME, make_token(password), httponly=True, samesite='Lax')
        raise response
    raise web.HTTPFound('/login?error=1')


async def logout(request: web.Request) -> web.Response:
    response = web.HTTPFound('/login')
    response.del_cookie(COOKIE_NAME)
    raise response
