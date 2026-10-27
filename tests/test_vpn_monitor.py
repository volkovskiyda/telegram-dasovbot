import unittest
from unittest.mock import MagicMock

import aiohttp

from dasovbot.services.vpn import VpnMonitor


class FakeResponse:
    def __init__(self, body=None, status=200):
        self.body = body or {}
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(MagicMock(), (), status=self.status, message='nope')

    async def json(self):
        return self.body


class FakeSession:
    """One canned answer per path; `replies` is mutated by the tests between polls."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        path = url.split('/v1', 1)[1]
        reply = self.replies['/v1' + path]
        if isinstance(reply, Exception):
            raise reply
        return reply


def ip_reply(ip, country='Netherlands', city='Amsterdam'):
    return FakeResponse({'public_ip': ip, 'country': country, 'city': city})


class TestVpnMonitor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.t = 0
        self.replies = {'/v1/publicip/ip': ip_reply('1.1.1.1'), '/v1/vpn/status': FakeResponse({'status': 'running'})}
        self.session = FakeSession(self.replies)
        self.monitor = VpnMonitor('http://127.0.0.1:8000/', 'key', session_factory=lambda: self.session,
                                  clock=lambda: f't{self.t}')

    async def test_first_poll_is_not_a_change(self):
        await self.monitor.poll_once()
        status = self.monitor.status
        self.assertEqual(status['ip'], '1.1.1.1')
        self.assertEqual(status['country'], 'Netherlands')
        self.assertEqual(status['city'], 'Amsterdam')
        self.assertEqual(status['status'], 'running')
        self.assertTrue(status['last_poll_ok'])
        self.assertEqual(status['last_poll_at'], 't0')
        self.assertIsNone(status['last_change_at'])
        self.assertEqual(status['changes_since_start'], 0)
        # Both routes carry the key header, and the base URL lost its trailing slash
        self.assertEqual(self.session.calls[0], ('http://127.0.0.1:8000/v1/publicip/ip', {'X-API-Key': 'key'}))
        self.assertEqual(self.session.calls[1][0], 'http://127.0.0.1:8000/v1/vpn/status')

    async def test_same_ip_is_not_a_change(self):
        await self.monitor.poll_once()
        self.t = 1
        await self.monitor.poll_once()
        self.assertIsNone(self.monitor.status['last_change_at'])
        self.assertEqual(self.monitor.status['changes_since_start'], 0)
        self.assertEqual(self.monitor.status['last_poll_at'], 't1')

    async def test_new_ip_counts_as_a_reconnect(self):
        await self.monitor.poll_once()
        self.t = 1
        self.replies['/v1/publicip/ip'] = ip_reply('2.2.2.2', country='Sweden', city='Stockholm')
        await self.monitor.poll_once()
        status = self.monitor.status
        self.assertEqual(status['ip'], '2.2.2.2')
        self.assertEqual(status['country'], 'Sweden')
        self.assertEqual(status['last_change_at'], 't1')
        self.assertEqual(status['changes_since_start'], 1)

    async def test_failed_poll_keeps_the_last_exit(self):
        await self.monitor.poll_once()
        self.t = 1
        self.replies['/v1/publicip/ip'] = aiohttp.ClientConnectionError('refused')
        with self.assertLogs('dasovbot.services.vpn', 'WARNING') as logs:
            await self.monitor.poll_once()
        self.assertIn('vpn poll failed', logs.output[0])
        status = self.monitor.status
        self.assertFalse(status['last_poll_ok'])
        self.assertEqual(status['last_poll_at'], 't1')
        self.assertEqual(status['ip'], '1.1.1.1')
        self.assertEqual(status['country'], 'Netherlands')
        self.assertEqual(status['changes_since_start'], 0)

    async def test_recovery_after_a_failure_counts_as_a_reconnect(self):
        await self.monitor.poll_once()
        self.replies['/v1/publicip/ip'] = aiohttp.ClientConnectionError('refused')
        await self.monitor.poll_once()
        self.t = 2
        self.replies['/v1/publicip/ip'] = ip_reply('1.1.1.1')
        await self.monitor.poll_once()
        status = self.monitor.status
        self.assertTrue(status['last_poll_ok'])
        self.assertEqual(status['last_change_at'], 't2')
        self.assertEqual(status['changes_since_start'], 1)

    async def test_first_poll_failing_then_succeeding_is_not_a_reconnect(self):
        # Nothing was known before, so the first good answer is just the baseline
        self.replies['/v1/publicip/ip'] = aiohttp.ClientConnectionError('refused')
        await self.monitor.poll_once()
        self.replies['/v1/publicip/ip'] = ip_reply('1.1.1.1')
        await self.monitor.poll_once()
        self.assertEqual(self.monitor.status['changes_since_start'], 0)
        self.assertIsNone(self.monitor.status['last_change_at'])
        self.assertTrue(self.monitor.status['last_poll_ok'])

    async def test_auth_error_is_logged_once(self):
        self.replies['/v1/publicip/ip'] = FakeResponse(status=401)
        with self.assertLogs('dasovbot.services.vpn', 'ERROR') as logs:
            await self.monitor.poll_once()
            await self.monitor.poll_once()
        self.assertEqual(len(logs.records), 1)
        self.assertIn('VPN_CONTROL_APIKEY', logs.output[0])
        self.assertFalse(self.monitor.status['last_poll_ok'])

    async def test_status_route_failure_is_a_failed_poll(self):
        self.replies['/v1/vpn/status'] = FakeResponse(status=500)
        await self.monitor.poll_once()
        self.assertFalse(self.monitor.status['last_poll_ok'])
        self.assertIsNone(self.monitor.status['ip'])
