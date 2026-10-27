"""dasovbot CLI: a stdlib-only client for the admin API (README "CLI").

Installed by `pipx install -e .` without the bot's dependencies, so this module
imports nothing from the package and nothing outside the standard library.
It knows both HA nodes, probes `/health` to find the active one, sends writes
there (retrying once when the role moved under it) and prints JSON.

Exit codes: 0 ok, 1 the server refused (its `{error, detail}` is on stderr),
2 no node answered / no active node, 3 not configured.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / '.config' / 'dasovbot' / 'config.toml'
PROBE_TIMEOUT_SEC = 5
REQUEST_TIMEOUT_SEC = 15
WAIT_POLL_SEC = 5
WAIT_DEFAULT_SEC = 600

EXIT_OK = 0
EXIT_SERVER = 1
EXIT_NO_NODE = 2
EXIT_CONFIG = 3


class CliError(Exception):
    """Printed to stderr as JSON; `code` becomes the exit status."""

    def __init__(self, payload: dict, code: int):
        super().__init__(payload.get('error', 'error'))
        self.payload = payload
        self.code = code


class Unreachable(Exception):
    def __init__(self, base: str, detail: str):
        super().__init__(f'{base}: {detail}')
        self.base = base
        self.detail = detail


@dataclass
class Settings:
    urls: list[str]
    token: str


@dataclass
class Node:
    url: str
    health: dict | None
    error: str = ''

    @property
    def reachable(self) -> bool:
        return self.health is not None

    @property
    def role(self) -> str | None:
        return self.health.get('role') if self.health else None

    def summary(self) -> dict:
        entry = {'url': self.url, 'reachable': self.reachable, 'role': self.role}
        if self.error:
            entry['error'] = self.error
        return entry


# --- configuration ---

def _split_urls(value: str) -> list[str]:
    return [item.strip().rstrip('/') for item in value.split(',') if item.strip()]


def load_settings(env=None, path=None) -> Settings:
    """`DASOVBOT_URLS` / `DASOVBOT_ADMIN_TOKEN` win per key over `~/.config/dasovbot/config.toml`."""
    env = os.environ if env is None else env
    path = CONFIG_PATH if path is None else Path(path)
    file_urls: list[str] = []
    file_token = ''
    if path.is_file():
        try:
            with open(path, 'rb') as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise CliError({'error': 'bad config file', 'detail': f'{path}: {exc}'}, EXIT_CONFIG)
        urls = data.get('urls', [])
        file_urls = _split_urls(urls) if isinstance(urls, str) else [str(item).rstrip('/') for item in urls]
        file_token = str(data.get('admin_token') or '')
    urls = _split_urls(env.get('DASOVBOT_URLS') or '') or file_urls
    token = (env.get('DASOVBOT_ADMIN_TOKEN') or '').strip() or file_token
    if not urls or not token:
        raise CliError({
            'error': 'not configured',
            'detail': 'set DASOVBOT_URLS (comma-separated) and DASOVBOT_ADMIN_TOKEN, '
                      f'or urls = [...] and admin_token = "..." in {path}',
        }, EXIT_CONFIG)
    return Settings(urls=urls, token=token)


# --- transport ---

def _decode(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {'error': 'bad response', 'detail': raw[:200].decode('utf-8', 'replace')}


class Client:
    def __init__(self, token: str):
        self.token = token

    def request(self, base: str, method: str, path: str, body=None, params=None,
                timeout: float = REQUEST_TIMEOUT_SEC, auth: bool = True) -> tuple[int, object]:
        """(status, decoded JSON). Non-2xx bodies come back the same way; a transport
        failure raises Unreachable."""
        url = base + path
        if params:
            query = {key: value for key, value in params.items() if value is not None}
            if query:
                url += '?' + urllib.parse.urlencode(query)
        headers = {'Accept': 'application/json'}
        if auth:
            headers['Authorization'] = f'Bearer {self.token}'
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, _decode(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise Unreachable(base, str(getattr(exc, 'reason', None) or exc))


def probe(client: Client, urls: list[str]) -> list[Node]:
    nodes = []
    for url in urls:
        try:
            status, body = client.request(url, 'GET', '/health', timeout=PROBE_TIMEOUT_SEC, auth=False)
        except Unreachable as exc:
            nodes.append(Node(url, None, exc.detail))
            continue
        if status == 200 and isinstance(body, dict):
            nodes.append(Node(url, body))
        else:
            nodes.append(Node(url, None, f'HTTP {status}'))
    return nodes


def active_nodes(nodes: list[Node]) -> list[Node]:
    return [node for node in nodes if node.role == 'active']


def _check(status: int, body) -> dict | list:
    if 200 <= status < 300:
        return body
    payload = body if isinstance(body, dict) else {'error': f'HTTP {status}', 'detail': str(body)}
    payload.setdefault('error', f'HTTP {status}')
    raise CliError(payload, EXIT_SERVER)


def read(client: Client, settings: Settings, path: str, params: dict | None = None):
    """GET from the first node that answers, the active one first."""
    nodes = probe(client, settings.urls)
    ordered = active_nodes(nodes) + [node for node in nodes if node.reachable and node.role != 'active']
    for node in ordered:
        try:
            status, body = client.request(node.url, 'GET', path, params=params)
        except Unreachable:
            continue
        return _check(status, body)
    raise CliError({'error': 'no node reachable', 'nodes': [node.summary() for node in nodes]}, EXIT_NO_NODE)


def write(client: Client, settings: Settings, path: str, body: dict) -> tuple[str, dict]:
    """POST to the active node; when the role moved between the probe and the
    POST (409 passive) re-probe once and retry on the new active node."""
    nodes = probe(client, settings.urls)
    active = active_nodes(nodes)
    if not active:
        raise CliError({'error': 'no active node', 'nodes': [node.summary() for node in nodes]}, EXIT_NO_NODE)
    base = active[0].url
    status, resp = _post(client, base, path, body)
    if status == 409 and isinstance(resp, dict) and resp.get('error') == 'passive':
        nodes = probe(client, settings.urls)
        active = active_nodes(nodes)
        if not active:
            raise CliError({'error': 'no active node', 'nodes': [node.summary() for node in nodes]}, EXIT_NO_NODE)
        base = active[0].url
        status, resp = _post(client, base, path, body)
    return base, _check(status, resp)


def _post(client: Client, base: str, path: str, body: dict) -> tuple[int, object]:
    try:
        return client.request(base, 'POST', path, body=body)
    except Unreachable as exc:
        raise CliError({'error': 'node unreachable', 'url': base, 'detail': exc.detail}, EXIT_NO_NODE)


# --- commands ---

def _chat(args, name: str) -> dict:
    value = getattr(args, name, None)
    return {'chat_id': value} if value else {}


def _flags(args) -> dict:
    out = {}
    if getattr(args, 'dry_run', False):
        out['dry_run'] = True
    if getattr(args, 'force', False):
        out['force'] = True
    return out


def cmd_status(args, settings: Settings, client: Client) -> tuple[list, int]:
    out = []
    for node in probe(client, settings.urls):
        entry = {'url': node.url, 'reachable': node.reachable, 'status': None}
        if node.reachable:
            try:
                status, body = client.request(node.url, 'GET', '/api/status')
            except Unreachable as exc:
                entry.update(reachable=False, error=exc.detail)
            else:
                if status == 200:
                    entry['status'] = body
                else:
                    entry['error'] = body
        else:
            entry['error'] = node.error
        out.append(entry)
    return out, EXIT_OK if any(entry['reachable'] for entry in out) else EXIT_NO_NODE


def cmd_users(args, settings, client):
    return read(client, settings, '/api/users', {'search': args.search, 'banned': args.banned}), EXIT_OK


def cmd_subscriptions(args, settings, client):
    return read(client, settings, '/api/subscriptions', {'chat_id': args.user}), EXIT_OK


def cmd_intents(args, settings, client):
    return read(client, settings, '/api/intents', {'ignored': args.ignored, 'url': args.url}), EXIT_OK


def cmd_alerts(args, settings, client):
    return read(client, settings, '/api/alerts'), EXIT_OK


def cmd_playlists(args, settings, client):
    return read(client, settings, '/api/playlists', {'url': args.url}), EXIT_OK


def cmd_ban(args, settings, client):
    target = urllib.parse.quote(args.target, safe='@')
    return write(client, settings, f'/api/users/{target}/{args.command}', _flags(args))[1], EXIT_OK


def cmd_subscribe(args, settings, client):
    body = {'url': args.url, **_chat(args, 'user'), **_flags(args)}
    if args.populate:
        body['populate'] = True
    return write(client, settings, '/api/subscriptions', body)[1], EXIT_OK


def cmd_unsubscribe(args, settings, client):
    body = {'url': args.url, **_chat(args, 'user'), **_flags(args)}
    return write(client, settings, '/api/subscriptions/remove', body)[1], EXIT_OK


def cmd_send(args, settings, client):
    body = {'url': args.url, **_chat(args, 'to'), **_flags(args)}
    base, queued = write(client, settings, '/api/intents', body)
    if args.wait is None or args.dry_run:
        return queued, EXIT_OK
    return wait_for_delivery(client, base, args.url, args.wait, queued)


def wait_for_delivery(client: Client, base: str, url: str, wait_sec: float, queued: dict) -> tuple[dict, int]:
    """Poll the node that queued the intent until it is delivered (popped, file
    cached), dropped (`absent`), ignored, or `wait_sec` ran out."""
    deadline = time.monotonic() + wait_sec
    polls = 0
    lookup: dict = {}
    while True:
        polls += 1
        try:
            status, body = client.request(base, 'GET', '/api/intents', params={'url': url})
            lookup = body if status == 200 and isinstance(body, dict) else {}
        except Unreachable:
            lookup = {}
        state = lookup.get('status')
        if state == 'delivered':
            result = 'delivered'
        elif state == 'absent':
            result = 'failed'
        elif state == 'ignored':
            result = 'ignored'
        elif time.monotonic() >= deadline:
            result = 'timeout'
        else:
            time.sleep(WAIT_POLL_SEC)
            continue
        break
    out = {'url': url, 'result': result, 'polls': polls, 'intent': lookup.get('intent'), 'queued': queued}
    return out, EXIT_OK if result == 'delivered' else EXIT_SERVER


def cmd_populate(args, settings, client):
    body = {'url': args.url} if args.url else {}
    return write(client, settings, '/api/populate', body)[1], EXIT_OK


COMMANDS = {
    'status': cmd_status, 'users': cmd_users, 'subscriptions': cmd_subscriptions, 'intents': cmd_intents,
    'alerts': cmd_alerts, 'playlists': cmd_playlists, 'ban': cmd_ban, 'unban': cmd_ban,
    'subscribe': cmd_subscribe, 'unsubscribe': cmd_unsubscribe, 'send': cmd_send, 'populate': cmd_populate,
}


# --- parser ---

def _write_flags(parser: argparse.ArgumentParser):
    parser.add_argument('--dry-run', action='store_true', help='resolve and report, write nothing')
    parser.add_argument('--force', action='store_true', help='accept a numeric chat id the bot has never seen')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='dasovbot',
        description='Operate dasovbot through its admin API: finds the active HA node, prints JSON.',
        epilog='Config: DASOVBOT_URLS (comma-separated) and DASOVBOT_ADMIN_TOKEN, or ~/.config/dasovbot/config.toml '
               'with urls = [...] and admin_token = "...". Exit 0 ok, 1 server refused, 2 no (active) node, 3 not configured.',
    )
    parser.add_argument('--pretty', action='store_true', help='indent the JSON output')
    sub = parser.add_subparsers(dest='command', metavar='COMMAND', required=True)

    sub.add_parser('status', help='health, counts, task last-runs, backup age and VPN exit, one block per node')

    p = sub.add_parser('users', help='known users, most requests first')
    p.add_argument('--search', help='match id, name or @username (case-insensitive)')
    p.add_argument('--banned', choices=['true', 'false'], help='only banned or only unbanned users')

    p = sub.add_parser('subscriptions', help='subscriptions with their subscribers')
    p.add_argument('--user', help='only this chat id or @username')

    p = sub.add_parser('intents', help='queued downloads by priority, or one URL\'s delivery state')
    p.add_argument('--ignored', choices=['true', 'false'], help='only ignored or only live intents')
    p.add_argument('--url', help='look one URL up: pending, retrying, ignored, delivered or absent')

    sub.add_parser('alerts', help='active health alerts')

    p = sub.add_parser('playlists', help='what /subscribe would offer for a video, playlist or channel URL')
    p.add_argument('url')

    for name, text in (('ban', 'ban a user (audited in the developer chat)'),
                       ('unban', 'unban a user (audited in the developer chat)')):
        p = sub.add_parser(name, help=text)
        p.add_argument('target', help='numeric id or @username')
        _write_flags(p)

    p = sub.add_parser('subscribe', help='subscribe a chat to a channel feed or playlist')
    p.add_argument('url', help='channel (its Videos feed), one of its feeds/playlists, or a playlist URL')
    p.add_argument('--user', help='chat id or @username; default DEVELOPER_CHAT_ID')
    p.add_argument('--populate', action='store_true', help='queue the feed\'s latest videos for that chat now')
    _write_flags(p)

    p = sub.add_parser('unsubscribe', help='remove one chat from a subscription (audited)')
    p.add_argument('url', help='a stored subscription key (see subscriptions)')
    p.add_argument('--user', help='chat id or @username; default DEVELOPER_CHAT_ID')
    _write_flags(p)

    p = sub.add_parser('send', help='download a video to a chat through the intent queue')
    p.add_argument('url')
    p.add_argument('--to', help='chat id or @username; default DEVELOPER_CHAT_ID')
    p.add_argument('--wait', nargs='?', const=WAIT_DEFAULT_SEC, type=float, metavar='SECONDS',
                   help=f'poll every {WAIT_POLL_SEC}s until delivered, failed or ignored (default {WAIT_DEFAULT_SEC}s)')
    _write_flags(p)

    p = sub.add_parser('populate', help='run the subscription poll now, for all or one subscription')
    p.add_argument('url', nargs='?', help='a stored subscription key; omit for every subscription')
    return parser


def render_help() -> str:
    """The top-level help followed by every subcommand's, in declaration order (the skill embeds it)."""
    parser = build_parser()
    parts = [parser.format_help()]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            parts.extend(child.format_help() for child in action.choices.values())
    return '\n\n'.join(part.rstrip('\n') for part in parts) + '\n'


def emit(obj, pretty: bool, stream=None):
    print(json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None), file=stream or sys.stdout)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        client = Client(settings.token)
        out, code = COMMANDS[args.command](args, settings, client)
    except CliError as exc:
        emit(exc.payload, args.pretty, stream=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return 130
    emit(out, args.pretty)
    return code


if __name__ == '__main__':
    sys.exit(main())
