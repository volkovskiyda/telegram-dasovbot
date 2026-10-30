import hmac
import logging
import os
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web

logger = logging.getLogger(__name__)

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

# The read-only API_TOKEN is accepted only on these prefixes (the videos
# library the phone app reads, and the API description); every other /api
# route needs ADMIN_TOKEN, which is accepted everywhere under /api/
API_READ_ONLY_PATHS = ('/api/videos', '/api/openapi.json')
# Set on the request by the middleware: 'admin' or 'read'
API_ROLE_KEY = web.RequestKey('api_role', str)

_generated_password: str | None = None
_generated_api_token: str | None = None
_generated_admin_token: str | None = None
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


def get_admin_token() -> str:
    global _generated_admin_token
    token = os.getenv('ADMIN_TOKEN')
    if token:
        return token
    if _generated_admin_token is None:
        _generated_admin_token = secrets.token_urlsafe(32)
    return _generated_admin_token


def adopt_generated_secret(env_var: str, value: str):
    """Use a previously generated secret (read back from its data/ file at startup) instead of a new one.

    Without this every restart minted a fresh password and tokens whenever the
    env var was unset, so a CLI configured from `data/admin_token.txt` broke on
    the next redeploy.
    """
    global _generated_password, _generated_api_token, _generated_admin_token
    if env_var == 'DASHBOARD_PASSWORD':
        _generated_password = value
    elif env_var == 'API_TOKEN':
        _generated_api_token = value
    elif env_var == 'ADMIN_TOKEN':
        _generated_admin_token = value
    else:
        raise ValueError(f'unknown generated secret {env_var}')


def _bearer(request: web.Request) -> str:
    header = request.headers.get('Authorization', '')
    scheme, _, token = header.partition(' ')
    return token.strip() if scheme.lower() == 'bearer' else ''


def check_api_token(request: web.Request) -> bool:
    token = _bearer(request)
    if not token:
        return False
    return hmac.compare_digest(token.encode(), get_api_token().encode())


def check_admin_token(request: web.Request) -> bool:
    token = _bearer(request)
    if not token:
        return False
    return hmac.compare_digest(token.encode(), get_admin_token().encode())


def _read_only_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + '/') for prefix in API_READ_ONLY_PATHS)


def api_role(request: web.Request) -> str | None:
    """'admin' for ADMIN_TOKEN anywhere under /api/, 'read' for API_TOKEN on the
    read-only prefixes, None when the request must be refused."""
    if check_admin_token(request):
        return 'admin'
    if _read_only_path(request.path) and check_api_token(request):
        return 'read'
    return None


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


def _request_hosts(request: web.Request) -> set[str]:
    hosts = {request.host.lower()}
    if behind_proxy():
        forwarded = request.headers.get('X-Forwarded-Host', '')
        if forwarded:
            hosts.add(forwarded.split(',')[0].strip().lower())
    return hosts


def same_origin(request: web.Request) -> bool:
    """False when the browser says the request came from another site.

    Checked on every cookie-authenticated POST: the session cookie is
    SameSite=Lax, which still lets any page on a sibling subdomain or another
    port of the same host submit a form here. Browsers always send `Origin` on
    a cross-site POST (and on same-origin ones too); `Referer` is the
    fallback. A request carrying neither (curl, the tests) is let through.
    """
    for header in ('Origin', 'Referer'):
        value = request.headers.get(header, '').strip()
        if not value:
            continue
        if value == 'null':
            return False
        return urlsplit(value).netloc.lower() in _request_hosts(request)
    return True


FRAME_HEADERS = {'X-Frame-Options': 'DENY', 'Content-Security-Policy': "frame-ancestors 'none'"}


@web.middleware
async def csrf_middleware(request: web.Request, handler):
    """Refuse cross-site POSTs to the cookie-authenticated dashboard and forbid framing it."""
    if request.method == 'POST' and not request.path.startswith(('/api/', '/sync/')) and not same_origin(request):
        logger.warning('cross-site POST refused from %s for %s (origin %s)', client_ip(request), request.path,
                       request.headers.get('Origin') or request.headers.get('Referer'))
        return web.Response(status=403, content_type='text/plain', text='cross-site request refused')
    try:
        response = await handler(request)
    except web.HTTPException as e:
        e.headers.update(FRAME_HEADERS)
        raise
    response.headers.update(FRAME_HEADERS)
    return response


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
        # must get a JSON 401, never a redirect to the HTML login page. One
        # warning per failure (no rate limit): the tokens are long and random,
        # the log line is there to notice a leaked or stale client
        role = api_role(request)
        if role is None:
            logger.warning('api auth failed from %s for %s %s', client_ip(request), request.method, request.path)
            return web.json_response({'error': 'unauthorized'}, status=401)
        request[API_ROLE_KEY] = role
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
