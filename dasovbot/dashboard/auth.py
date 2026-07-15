import hmac
import os
import secrets
import time

from aiohttp import web


COOKIE_NAME = 'dasovbot_token'
SESSION_TTL_SEC = 7 * 24 * 60 * 60  # a week
MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW_SEC = 60

_generated_password: str | None = None
_sessions: dict[str, float] = {}
_failed_logins: dict[str, list[float]] = {}


def get_password() -> str:
    global _generated_password
    password = os.getenv('DASHBOARD_PASSWORD')
    if password:
        return password
    if _generated_password is None:
        _generated_password = secrets.token_urlsafe(16)
    return _generated_password


def secure_cookie() -> bool:
    return os.getenv('DASHBOARD_SECURE_COOKIE', 'false').lower() == 'true'


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL_SEC
    return token


def check_token(request: web.Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    expires = _sessions.get(token)
    if expires is None:
        return False
    if expires < time.time():
        del _sessions[token]
        return False
    return True


def _rate_limited(remote: str) -> bool:
    cutoff = time.time() - LOGIN_ATTEMPT_WINDOW_SEC
    attempts = [item for item in _failed_logins.get(remote, []) if item > cutoff]
    _failed_logins[remote] = attempts
    return len(attempts) >= MAX_LOGIN_ATTEMPTS


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
    remote = request.remote or ''
    if _rate_limited(remote):
        raise web.HTTPFound('/login?error=2')
    data = await request.post()
    password = data.get('password', '')
    expected = get_password()
    if hmac.compare_digest(password.encode(), expected.encode()):
        response = web.HTTPFound('/')
        response.set_cookie(
            COOKIE_NAME, create_session(), max_age=SESSION_TTL_SEC,
            httponly=True, secure=secure_cookie(), samesite='Lax',
        )
        raise response
    _failed_logins.setdefault(remote, []).append(time.time())
    raise web.HTTPFound('/login?error=1')


async def logout(request: web.Request) -> web.Response:
    _sessions.pop(request.cookies.get(COOKIE_NAME), None)
    response = web.HTTPFound('/login')
    response.del_cookie(COOKIE_NAME)
    raise response
