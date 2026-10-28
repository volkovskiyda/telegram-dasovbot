import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dasovbot import cli
from dasovbot.cli import Unreachable, load_settings, main, render_help

PRIMARY = 'http://primary:8080'
STANDBY = 'http://standby:8080'
ENV = {'DASOVBOT_URLS': f'{PRIMARY}/, {STANDBY}', 'DASOVBOT_ADMIN_TOKEN': 'tok'}


def health(role):
    return {'role': role, 'node': role, 'ready': True}


class FakeTransport:
    """Scripted `Client.request`: replies keyed by (base, method, path); a list is
    consumed one reply per call, an Unreachable instance is raised."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, client, base, method, path, body=None, params=None, timeout=None, auth=True):
        self.calls.append({'base': base, 'method': method, 'path': path, 'body': body, 'params': params,
                           'auth': auth, 'token': client.token})
        reply = self.replies.get((base, method, path))
        if reply is None:
            raise AssertionError(f'unscripted request {method} {base}{path}')
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def paths(self):
        return [(call['base'], call['method'], call['path']) for call in self.calls]


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict('os.environ', ENV, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def run_cli(self, argv, replies):
        transport = FakeTransport(replies)
        out, err = io.StringIO(), io.StringIO()
        def request(client, *args, **kwargs):
            return transport(client, *args, **kwargs)

        with patch.object(cli.Client, 'request', request), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue(), transport

    def run_json(self, argv, replies, expected_code=0):
        code, out, err, transport = self.run_cli(argv, replies)
        self.assertEqual(code, expected_code, err or out)
        return json.loads(out if out.strip() else err), transport


class TestSettings(unittest.TestCase):
    def test_env_wins_per_key_over_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            path.write_text('urls = ["http://a:1/", "http://b:2"]\nadmin_token = "from-file"\n')
            settings = load_settings(env={'DASOVBOT_ADMIN_TOKEN': 'from-env'}, path=path)
            self.assertEqual(settings.urls, ['http://a:1', 'http://b:2'])
            self.assertEqual(settings.token, 'from-env')
            settings = load_settings(env={'DASOVBOT_URLS': 'http://c:3'}, path=path)
            self.assertEqual(settings.urls, ['http://c:3'])
            self.assertEqual(settings.token, 'from-file')

    def test_missing_everything_is_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cli.CliError) as ctx:
                load_settings(env={}, path=Path(tmp) / 'absent.toml')
        self.assertEqual(ctx.exception.code, 3)
        self.assertEqual(ctx.exception.payload['error'], 'not configured')

    def test_broken_file_is_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            path.write_text('urls = [\n')
            with self.assertRaises(cli.CliError) as ctx:
                load_settings(env={}, path=path)
        self.assertEqual(ctx.exception.code, 3)
        self.assertEqual(ctx.exception.payload['error'], 'bad config file')

    def test_main_reports_missing_config_on_stderr(self):
        with patch.dict('os.environ', {}, clear=True), \
                patch.object(cli, 'CONFIG_PATH', Path('/nonexistent/config.toml')):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(['status'])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(err.getvalue())['error'], 'not configured')


class TestReads(CliTestCase):
    def test_read_prefers_the_active_node(self):
        body, transport = self.run_json(['users', '--search', '@bob', '--banned', 'true'], {
            (PRIMARY, 'GET', '/health'): (200, health('passive')),
            (STANDBY, 'GET', '/health'): (200, health('active')),
            (STANDBY, 'GET', '/api/users'): (200, [{'id': '1'}]),
        })
        self.assertEqual(body, [{'id': '1'}])
        call = transport.calls[-1]
        self.assertEqual(call['base'], STANDBY)
        self.assertEqual(call['params'], {'search': '@bob', 'banned': 'true'})
        self.assertEqual(call['token'], 'tok')
        self.assertTrue(call['auth'])
        # The probe is unauthenticated
        self.assertFalse(transport.calls[0]['auth'])

    def test_read_falls_back_to_a_passive_node(self):
        body, transport = self.run_json(['alerts'], {
            (PRIMARY, 'GET', '/health'): Unreachable(PRIMARY, 'refused'),
            (STANDBY, 'GET', '/health'): (200, health('passive')),
            (STANDBY, 'GET', '/api/alerts'): (200, []),
        })
        self.assertEqual(body, [])

    def test_read_with_no_node_is_exit_2(self):
        body, _ = self.run_json(['intents', '--url', 'https://x/v'], {
            (PRIMARY, 'GET', '/health'): Unreachable(PRIMARY, 'refused'),
            (STANDBY, 'GET', '/health'): Unreachable(STANDBY, 'timed out'),
        }, expected_code=2)
        self.assertEqual(body['error'], 'no node reachable')
        self.assertEqual([node['reachable'] for node in body['nodes']], [False, False])
        self.assertEqual(body['nodes'][1]['error'], 'timed out')

    def test_server_error_is_exit_1_with_the_body(self):
        body, _ = self.run_json(['playlists', 'ftp://x'], {
            (PRIMARY, 'GET', '/health'): (200, health('active')),
            (STANDBY, 'GET', '/health'): (200, health('passive')),
            (PRIMARY, 'GET', '/api/playlists'): (400, {'error': 'invalid url', 'detail': 'must be http(s)'}),
        }, expected_code=1)
        self.assertEqual(body, {'error': 'invalid url', 'detail': 'must be http(s)'})

    def test_status_prints_one_block_per_node(self):
        body, transport = self.run_json(['status'], {
            (PRIMARY, 'GET', '/health'): (200, health('active')),
            (PRIMARY, 'GET', '/api/status'): (200, {'role': 'active', 'vpn': None}),
            (STANDBY, 'GET', '/health'): Unreachable(STANDBY, 'refused'),
        })
        self.assertEqual(body, [
            {'url': PRIMARY, 'reachable': True, 'status': {'role': 'active', 'vpn': None}},
            {'url': STANDBY, 'reachable': False, 'status': None, 'error': 'refused'},
        ])

    def test_status_with_nothing_reachable_is_exit_2(self):
        body, _ = self.run_json(['status'], {
            (PRIMARY, 'GET', '/health'): Unreachable(PRIMARY, 'refused'),
            (STANDBY, 'GET', '/health'): Unreachable(STANDBY, 'refused'),
        }, expected_code=2)
        self.assertFalse(any(node['reachable'] for node in body))

    def test_pretty_indents(self):
        code, out, _, _ = self.run_cli(['--pretty', 'alerts'], {
            (PRIMARY, 'GET', '/health'): (200, health('active')),
            (STANDBY, 'GET', '/health'): (200, health('passive')),
            (PRIMARY, 'GET', '/api/alerts'): (200, [{'id': 'a'}]),
        })
        self.assertEqual(code, 0)
        self.assertEqual(out, '[\n  {\n    "id": "a"\n  }\n]\n')

    def test_pretty_after_the_command(self):
        for argv, expected in ((['alerts', '--pretty'], '[\n  {\n    "id": "a"\n  }\n]\n'),
                               (['--pretty', 'alerts'], '[\n  {\n    "id": "a"\n  }\n]\n'),
                               (['alerts'], '[{"id": "a"}]\n')):
            with self.subTest(argv=argv):
                code, out, _, _ = self.run_cli(argv, {
                    (PRIMARY, 'GET', '/health'): (200, health('active')),
                    (STANDBY, 'GET', '/health'): (200, health('passive')),
                    (PRIMARY, 'GET', '/api/alerts'): (200, [{'id': 'a'}]),
                })
                self.assertEqual(code, 0)
                self.assertEqual(out, expected)


class TestWrites(CliTestCase):
    def test_ban_goes_to_the_active_node(self):
        body, transport = self.run_json(['ban', '@bob', '--dry-run'], {
            (PRIMARY, 'GET', '/health'): (200, health('passive')),
            (STANDBY, 'GET', '/health'): (200, health('active')),
            (STANDBY, 'POST', '/api/users/@bob/ban'): (200, {'dry_run': True, 'action': 'ban'}),
        })
        self.assertEqual(body['action'], 'ban')
        self.assertEqual(transport.calls[-1]['body'], {'dry_run': True})

    def test_no_active_node_is_exit_2(self):
        body, transport = self.run_json(['unban', '7'], {
            (PRIMARY, 'GET', '/health'): (200, health('passive')),
            (STANDBY, 'GET', '/health'): Unreachable(STANDBY, 'refused'),
        }, expected_code=2)
        self.assertEqual(body['error'], 'no active node')
        self.assertEqual(body['nodes'][0], {'url': PRIMARY, 'reachable': True, 'role': 'passive'})
        self.assertNotIn('POST', [call['method'] for call in transport.calls])

    def test_409_passive_reprobes_once_and_retries(self):
        # The lease moved between the probe and the POST
        body, transport = self.run_json(['subscribe', 'https://x/c', '--user', '@ann', '--populate', '--force'], {
            (PRIMARY, 'GET', '/health'): [(200, health('active')), (200, health('passive'))],
            (STANDBY, 'GET', '/health'): [(200, health('passive')), (200, health('active'))],
            (PRIMARY, 'POST', '/api/subscriptions'): (409, {'error': 'passive', 'active': STANDBY}),
            (STANDBY, 'POST', '/api/subscriptions'): (200, {'action': 'subscribe'}),
        })
        self.assertEqual(body, {'action': 'subscribe'})
        posts = [call for call in transport.calls if call['method'] == 'POST']
        self.assertEqual([call['base'] for call in posts], [PRIMARY, STANDBY])
        self.assertEqual(posts[1]['body'], {'url': 'https://x/c', 'chat_id': '@ann', 'force': True, 'populate': True})

    def test_409_other_than_passive_is_not_retried(self):
        body, transport = self.run_json(['ban', '@bob'], {
            (PRIMARY, 'GET', '/health'): (200, health('active')),
            (STANDBY, 'GET', '/health'): (200, health('passive')),
            (PRIMARY, 'POST', '/api/users/@bob/ban'): (409, {'error': 'ambiguous user', 'candidates': []}),
        }, expected_code=1)
        self.assertEqual(body['error'], 'ambiguous user')
        self.assertEqual(len([call for call in transport.calls if call['method'] == 'POST']), 1)

    def test_unsubscribe_and_populate_bodies(self):
        replies = {
            (PRIMARY, 'GET', '/health'): (200, health('active')),
            (STANDBY, 'GET', '/health'): (200, health('passive')),
            (PRIMARY, 'POST', '/api/subscriptions/remove'): (200, {'action': 'remove_subscriber'}),
            (PRIMARY, 'POST', '/api/populate'): (200, {'status': 'started'}),
        }
        _, transport = self.run_json(['unsubscribe', 'https://x/c/videos', '--user', '7'], replies)
        self.assertEqual(transport.calls[-1]['body'], {'url': 'https://x/c/videos', 'chat_id': '7'})
        _, transport = self.run_json(['populate'], replies)
        self.assertEqual(transport.calls[-1]['body'], {})
        _, transport = self.run_json(['populate', 'https://x/c/videos'], replies)
        self.assertEqual(transport.calls[-1]['body'], {'url': 'https://x/c/videos'})


class TestSendWait(CliTestCase):
    def _replies(self, lookups):
        return {
            (PRIMARY, 'GET', '/health'): (200, health('active')),
            (STANDBY, 'GET', '/health'): (200, health('passive')),
            (PRIMARY, 'POST', '/api/intents'): (200, {'action': 'queue', 'url': 'https://x/v', 'cached': False}),
            (PRIMARY, 'GET', '/api/intents'): [(200, lookup) for lookup in lookups],
        }

    def test_without_wait_prints_the_queue_result(self):
        body, transport = self.run_json(['send', 'https://x/v', '--to', '@ann'], self._replies([]))
        self.assertEqual(body['action'], 'queue')
        self.assertEqual(transport.calls[-1]['body'], {'url': 'https://x/v', 'chat_id': '@ann'})

    @patch('dasovbot.cli.time.sleep')
    def test_wait_until_delivered(self, mock_sleep):
        pending = {'url': 'https://x/v', 'status': 'pending', 'intent': {'priority': 1}}
        delivered = {'url': 'https://x/v', 'status': 'delivered', 'intent': None}
        body, transport = self.run_json(['send', 'https://x/v', '--wait'], self._replies([pending, pending, delivered]))
        self.assertEqual(body['result'], 'delivered')
        self.assertEqual(body['polls'], 3)
        self.assertEqual(body['queued']['action'], 'queue')
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertEqual(transport.calls[-1]['params'], {'url': 'https://x/v'})

    @patch('dasovbot.cli.time.sleep')
    def test_wait_reports_a_dropped_intent_as_failed(self, mock_sleep):
        absent = {'url': 'https://x/v', 'status': 'absent', 'intent': None}
        body, _ = self.run_json(['send', 'https://x/v', '--wait', '30'], self._replies([absent]), expected_code=1)
        self.assertEqual(body['result'], 'failed')
        mock_sleep.assert_not_called()

    @patch('dasovbot.cli.time.sleep')
    def test_wait_reports_ignored(self, mock_sleep):
        ignored = {'url': 'https://x/v', 'status': 'ignored', 'intent': {'status': 'ignored'}}
        body, _ = self.run_json(['send', 'https://x/v', '--wait'], self._replies([ignored]), expected_code=1)
        self.assertEqual(body['result'], 'ignored')
        self.assertEqual(body['intent'], {'status': 'ignored'})

    @patch('dasovbot.cli.time.sleep')
    def test_wait_times_out(self, mock_sleep):
        pending = {'url': 'https://x/v', 'status': 'pending', 'intent': {'priority': 1}}
        body, _ = self.run_json(['send', 'https://x/v', '--wait', '0'], self._replies([pending]), expected_code=1)
        self.assertEqual(body['result'], 'timeout')
        self.assertEqual(body['polls'], 1)
        mock_sleep.assert_not_called()

    @patch('dasovbot.cli.time.sleep')
    def test_dry_run_does_not_wait(self, mock_sleep):
        replies = self._replies([])
        replies[(PRIMARY, 'POST', '/api/intents')] = (200, {'dry_run': True, 'action': 'queue'})
        body, _ = self.run_json(['send', 'https://x/v', '--wait', '--dry-run'], replies)
        self.assertEqual(body, {'dry_run': True, 'action': 'queue'})


class TestHelp(unittest.TestCase):
    def test_render_help_covers_every_command(self):
        text = render_help()
        for name in cli.COMMANDS:
            self.assertIn(f'usage: dasovbot {name}', text)
        self.assertTrue(text.startswith('usage: dasovbot'))


class TestDependencyFreedom(unittest.TestCase):
    def test_import_loads_no_third_party_package(self):
        # In a fresh interpreter, with the repo on the path and nothing else
        root = Path(__file__).resolve().parent.parent
        code = ('import sys; import dasovbot.cli; '
                'bad = {"aiohttp", "telegram", "yt_dlp", "aiosqlite", "dotenv"} & set(sys.modules); '
                'print(sorted(bad))')
        result = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True,
                                env={**os.environ, 'PYTHONPATH': str(root)}, check=True)
        self.assertEqual(result.stdout.strip(), '[]')
