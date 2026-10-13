import hmac
import os
import secrets
import time

from aiohttp import web


COOKIE_NAME = 'dasovbot_token'
SESSION_TTL_SEC = 7 * 24 * 60 * 60  # a week
MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW_SEC = 60
# Behind a proxy the limit is keyed on the X-Forwarded-For client so one
# user's failures never lock out the others, but the dashboard port is also
# published directly on the LAN: a client that reaches it without the proxy
# can forge a new X-Forwarded-For per attempt. The socket peer therefore gets
# a second, more generous budget that such a client cannot rotate out of
MAX_LOGIN_ATTEMPTS_PER_REMOTE = 5 * MAX_LOGIN_ATTEMPTS

_generated_password: str | None = None
_generated_api_token: str | None = None
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


def get_api_token() -> str:
    global _generated_api_token
    token = os.getenv('API_TOKEN')
    if token:
        return token
    if _generated_api_token is None:
        _generated_api_token = secrets.token_urlsafe(32)
    return _generated_api_token


def _bearer(request: web.Request) -> str:
    header = request.headers.get('Authorization', '')
    scheme, _, token = header.partition(' ')
    return token.strip() if scheme.lower() == 'bearer' else ''


def check_api_token(request: web.Request) -> bool:
    token = _bearer(request)
    if not token:
        return False
    return hmac.compare_digest(token.encode(), get_api_token().encode())


def get_sync_secret() -> str:
    # No auto-generation: both HA nodes must share the value, so it can only
    # come from the environment. Empty means the /sync/ endpoints are off
    return os.getenv('SYNC_SECRET') or ''


def check_sync_token(request: web.Request) -> bool:
    secret = get_sync_secret()
    token = _bearer(request)
    if not secret or not token:
        return False
    return hmac.compare_digest(token.encode(), secret.encode())


def behind_proxy() -> bool:
    return os.getenv('DASHBOARD_BEHIND_PROXY', 'false').lower() == 'true'


def secure_cookie(request: web.Request) -> bool:
    # The dashboard itself only speaks plain HTTP, so the session cookie can
    # only be marked Secure when a trusted proxy terminates TLS in front of it
    return behind_proxy() and request.headers.get('X-Forwarded-Proto', '').lower() == 'https'


def client_ip(request: web.Request) -> str:
    if behind_proxy():
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            # The rightmost entry was appended by our own reverse proxy and
            # cannot be spoofed by the client; earlier entries can be forged
            return forwarded.split(',')[-1].strip()
    return request.remote or ''


def _sweep_expired_sessions():
    # Expired tokens from clients that never return would accumulate forever
    cutoff = time.time()
    for token, expires in list(_sessions.items()):
        if expires < cutoff:
            _sessions.pop(token, None)


def create_session() -> str:
    _sweep_expired_sessions()
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


def _sweep_failed_logins():
    # Entries were only pruned when the same IP retried, so a scan across
    # many IPs would grow the dict forever
    cutoff = time.time() - LOGIN_ATTEMPT_WINDOW_SEC
    for remote, attempts in list(_failed_logins.items()):
        recent = [item for item in attempts if item > cutoff]
        if recent:
            _failed_logins[remote] = recent
        else:
            del _failed_logins[remote]


def _rate_limited(remote: str, limit: int = MAX_LOGIN_ATTEMPTS) -> bool:
    cutoff = time.time() - LOGIN_ATTEMPT_WINDOW_SEC
    attempts = [item for item in _failed_logins.get(remote, []) if item > cutoff]
    if attempts:
        _failed_logins[remote] = attempts
    else:
        _failed_logins.pop(remote, None)
    return len(attempts) >= limit


def _remote_key(request: web.Request, client: str) -> str | None:
    """The socket peer's limiter key, or None when it is the client key already."""
    remote = request.remote or ''
    if not remote or remote == client:
        return None
    return f'remote:{remote}'


def login_rate_limited(request: web.Request) -> bool:
    client = client_ip(request)
    if _rate_limited(client):
        return True
    remote_key = _remote_key(request, client)
    return remote_key is not None and _rate_limited(remote_key, MAX_LOGIN_ATTEMPTS_PER_REMOTE)


def record_failed_login(request: web.Request):
    now = time.time()
    client = client_ip(request)
    _failed_logins.setdefault(client, []).append(now)
    remote_key = _remote_key(request, client)
    if remote_key is not None:
        _failed_logins.setdefault(remote_key, []).append(now)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    # Static assets stay public so the login page can load its favicon;
    # /health is the Docker healthcheck and the peer's liveness probe
    if request.path in ('/login', '/health') or request.path.startswith('/static/'):
        return await handler(request)
    if request.path.startswith('/sync/'):
        # Peer-to-peer endpoints: a separate shared secret, never the API
        # token or a session. Nothing about the failure is logged beyond the
        # path (the access log is WARNING-only)
        if not check_sync_token(request):
            return web.json_response({'error': 'unauthorized'}, status=401)
        return await handler(request)
    if request.path.startswith('/api/'):
        # Machine clients authenticate per-request with a bearer token and
        # must get a JSON 401, never a redirect to the HTML login page
        if not check_api_token(request):
            return web.json_response({'error': 'unauthorized'}, status=401)
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
    _sweep_failed_logins()
    if login_rate_limited(request):
        raise web.HTTPFound('/login?error=2')
    data = await request.post()
    password = data.get('password', '')
    expected = get_password()
    if hmac.compare_digest(password.encode(), expected.encode()):
        response = web.HTTPFound('/')
        response.set_cookie(
            COOKIE_NAME, create_session(), max_age=SESSION_TTL_SEC,
            httponly=True, secure=secure_cookie(request), samesite='Lax',
        )
        raise response
    record_failed_login(request)
    raise web.HTTPFound('/login?error=1')


async def logout(request: web.Request) -> web.Response:
    _sessions.pop(request.cookies.get(COOKIE_NAME), None)
    response = web.HTTPFound('/login')
    response.del_cookie(COOKIE_NAME)
    raise response
