import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from dasovbot.constants import DATETIME_FORMAT
from dasovbot.dashboard.server import format_duration
from dasovbot.dashboard.views import parse_timestamp, relative_time, retry_ignored, remove_ignored
from dasovbot.models import Intent, TemporaryInlineQuery
from tests.helpers import make_state, make_config


class TestFormatDuration(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_duration(0), '0:00')

    def test_none(self):
        self.assertEqual(format_duration(None), '0:00')

    def test_seconds(self):
        self.assertEqual(format_duration(45), '0:45')

    def test_minutes(self):
        self.assertEqual(format_duration(125), '2:05')

    def test_hours(self):
        self.assertEqual(format_duration(3661), '1:01:01')

    def test_exact_minute(self):
        self.assertEqual(format_duration(60), '1:00')

    def test_exact_hour(self):
        self.assertEqual(format_duration(3600), '1:00:00')


class TestParseTimestamp(unittest.TestCase):
    def test_valid(self):
        ts = '20240101_120000'
        result = parse_timestamp(ts)
        self.assertIsInstance(result, datetime)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.hour, 12)

    def test_none(self):
        self.assertIsNone(parse_timestamp(None))

    def test_empty(self):
        self.assertIsNone(parse_timestamp(''))

    def test_invalid(self):
        self.assertIsNone(parse_timestamp('not-a-timestamp'))


class TestRelativeTime(unittest.TestCase):
    def test_none_returns_never(self):
        self.assertEqual(relative_time(None), 'never')

    def test_seconds_ago(self):
        ts = (datetime.now() - timedelta(seconds=30)).strftime(DATETIME_FORMAT)
        result = relative_time(ts)
        self.assertIn('s ago', result)

    def test_minutes_ago(self):
        ts = (datetime.now() - timedelta(minutes=5)).strftime(DATETIME_FORMAT)
        result = relative_time(ts)
        self.assertIn('m ago', result)

    def test_hours_ago(self):
        ts = (datetime.now() - timedelta(hours=3)).strftime(DATETIME_FORMAT)
        result = relative_time(ts)
        self.assertIn('h ago', result)

    def test_days_ago(self):
        ts = (datetime.now() - timedelta(days=2)).strftime(DATETIME_FORMAT)
        result = relative_time(ts)
        self.assertIn('d ago', result)


class TestRetryIgnored(unittest.IsolatedAsyncioTestCase):
    def _make_request(self, state, post_data):
        request = MagicMock()
        request.app = {'state': state}
        request.post = AsyncMock(return_value=post_data)
        return request

    @patch('dasovbot.database.upsert_intent', new_callable=AsyncMock)
    async def test_retries_intent(self, mock_upsert):
        intent = Intent(ignored=True, chat_ids=['1'])
        state = make_state(intents={'url1': intent})
        request = self._make_request(state, {'url': 'url1', 'type': 'intent'})
        with self.assertRaises(web.HTTPFound):
            await retry_ignored(request)
        self.assertFalse(intent.ignored)
        self.assertFalse(state.download_queue.empty())

    async def test_retries_inline(self):
        tiq = TemporaryInlineQuery(ignored=True)
        state = make_state(temporary_inline_queries={'url1': tiq})
        request = self._make_request(state, {'url': 'url1', 'type': 'inline'})
        with self.assertRaises(web.HTTPFound):
            await retry_ignored(request)
        self.assertFalse(tiq.ignored)

    async def test_missing_url(self):
        state = make_state()
        request = self._make_request(state, {'url': '', 'type': 'intent'})
        with self.assertRaises(web.HTTPFound):
            await retry_ignored(request)


class TestRemoveIgnored(unittest.IsolatedAsyncioTestCase):
    def _make_request(self, state, post_data):
        request = MagicMock()
        request.app = {'state': state}
        request.post = AsyncMock(return_value=post_data)
        return request

    @patch('dasovbot.database.delete_intent', new_callable=AsyncMock)
    async def test_removes_intent(self, mock_delete):
        intent = Intent(ignored=True)
        state = make_state(intents={'url1': intent})
        request = self._make_request(state, {'url': 'url1', 'type': 'intent'})
        with self.assertRaises(web.HTTPFound):
            await remove_ignored(request)
        self.assertNotIn('url1', state.intents)

    async def test_removes_inline(self):
        tiq = TemporaryInlineQuery(ignored=True)
        state = make_state(temporary_inline_queries={'url1': tiq})
        request = self._make_request(state, {'url': 'url1', 'type': 'inline'})
        with self.assertRaises(web.HTTPFound):
            await remove_ignored(request)
        self.assertNotIn('url1', state.temporary_inline_queries)

    async def test_missing_url(self):
        state = make_state()
        request = self._make_request(state, {'url': '', 'type': 'intent'})
        with self.assertRaises(web.HTTPFound):
            await remove_ignored(request)


if __name__ == '__main__':
    unittest.main()
