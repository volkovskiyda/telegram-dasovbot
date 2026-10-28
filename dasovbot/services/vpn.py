"""VPN status poller: asks the gluetun control server for the exit IP and tunnel state.

Runs with the dashboard on both HA roles (it describes this host, not the
active node), keeps everything in memory and never alerts: the `vpn` block
of `/api/status` is its only consumer. A reconnect is a public IP that
changed between two good polls, or a good poll after a failed one.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from dasovbot.constants import VPN_POLL_INTERVAL_SEC, VPN_POLL_TIMEOUT_SEC
from dasovbot.helpers import now

logger = logging.getLogger(__name__)


class VpnMonitor:
    def __init__(self, url: str, apikey: str, session_factory=aiohttp.ClientSession, clock=now,
                 interval: float = VPN_POLL_INTERVAL_SEC):
        self.url = url.rstrip('/')
        self._headers = {'X-API-Key': apikey}
        self._session_factory = session_factory
        self._clock = clock
        self.interval = interval
        self._auth_error_logged = False
        self.status: dict = {
            'ip': None, 'country': None, 'city': None, 'status': None,
            'last_change_at': None, 'changes_since_start': 0,
            'last_poll_ok': None, 'last_poll_at': None,
        }

    async def _get(self, session: aiohttp.ClientSession, path: str) -> dict:
        timeout = aiohttp.ClientTimeout(total=VPN_POLL_TIMEOUT_SEC)
        async with session.get(f'{self.url}{path}', headers=self._headers, timeout=timeout) as resp:
            resp.raise_for_status()
            # gluetun serves its JSON as text/plain; aiohttp rejects that unless the check is off
            return await resp.json(content_type=None)

    async def poll_once(self) -> None:
        try:
            async with self._session_factory() as session:
                ip_info = await self._get(session, '/v1/publicip/ip')
                vpn_info = await self._get(session, '/v1/vpn/status')
        except Exception as exc:  # noqa: BLE001 — the poller must outlive every transport error
            if isinstance(exc, aiohttp.ClientResponseError) and exc.status in (401, 403):
                if not self._auth_error_logged:
                    logger.error('vpn poll rejected (%s): VPN_CONTROL_APIKEY is not accepted by the control server',
                                 exc.status)
                    self._auth_error_logged = True
            else:
                logger.warning('vpn poll failed: %s', exc)
                logger.debug('vpn poll failure', exc_info=True)
            # Keep the last known exit so the status still shows where we were
            self.status['last_poll_ok'] = False
            self.status['last_poll_at'] = self._clock()
            return

        new_ip = ip_info.get('public_ip') or None
        previous_ip = self.status['ip']
        # The first good poll is the baseline, never a change
        reconnected = previous_ip is not None and (new_ip != previous_ip or self.status['last_poll_ok'] is False)
        if reconnected:
            self.status['last_change_at'] = self._clock()
            self.status['changes_since_start'] += 1
        self.status.update({
            'ip': new_ip,
            'country': ip_info.get('country') or None,
            'city': ip_info.get('city') or None,
            'status': vpn_info.get('status') or None,
            'last_poll_ok': True,
            'last_poll_at': self._clock(),
        })

    async def run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.interval)
