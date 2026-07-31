import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dasovbot.models import Subscription, TemporaryInlineQuery, VideoInfo
from dasovbot.constants import RESTART_DELAY_SEC
from dasovbot.services.background import (
    populate_animation, populate_video, populate_playlist, run_populate_subscriptions,
    populate_subscriptions, clear_temporary_inline_queries, monitor_backups, newest_backup_age,
    start_background_tasks, stop_background_tasks, run_forever, _on_task_done,
)
from tests.helpers import make_state, make_config


def make_video_info(**overrides):
    defaults = dict(
        title='title', filepath='/media/video.mp4', filename='video.mp4',
        duration=10, width=640, height=360, caption='caption',
    )
    defaults.update(overrides)
    return VideoInfo(**defaults)


class TestPopulateAnimation(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_skips_when_already_set(self, mock_extract):
        state = make_state(animation_file_id='anim123')
        await populate_animation(AsyncMock(), state)
        mock_extract.assert_not_awaited()

    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_skips_when_no_loading_video_id(self, mock_extract):
        state = make_state(animation_file_id=None, config=make_config(loading_video_id=''))
        await populate_animation(AsyncMock(), state)
        mock_extract.assert_not_awaited()

    @patch('dasovbot.services.background.post_process', new_callable=AsyncMock, return_value='file123')
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_success_sets_animation_file_id(self, mock_extract, mock_post):
        mock_extract.return_value = make_video_info()
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        await populate_animation(bot, state)
        bot.send_video.assert_awaited_once()
        self.assertEqual(state.animation_file_id, 'file123')

    @patch('dasovbot.services.background.post_process', new_callable=AsyncMock, return_value='file123')
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_upload_waits_for_semaphore(self, mock_extract, mock_post):
        mock_extract.return_value = make_video_info()
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()

        await state.upload_semaphore.acquire()
        task = asyncio.create_task(populate_animation(bot, state))
        try:
            for _ in range(5):
                await asyncio.sleep(0)
            bot.send_video.assert_not_awaited()
        finally:
            state.upload_semaphore.release()
        await task
        bot.send_video.assert_awaited_once()
        self.assertEqual(state.animation_file_id, 'file123')

    @patch('dasovbot.services.background.post_process', new_callable=AsyncMock, return_value='file123')
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_uploads_by_filesystem_path(self, mock_extract, mock_post):
        # A str path keeps PTB local mode in effect (file:// hand-off);
        # bytes or a file object would load the video into bot memory
        mock_extract.return_value = make_video_info()
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        await populate_animation(bot, state)
        video_arg = bot.send_video.await_args.kwargs['video']
        self.assertIsInstance(video_arg, str)
        self.assertEqual(video_arg, '/media/video.mp4')

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.post_process', new_callable=AsyncMock, return_value='file123')
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_retries_after_failed_extraction(self, mock_extract, mock_post, mock_sleep):
        mock_extract.side_effect = [None, make_video_info()]
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        await populate_animation(bot, state)
        self.assertEqual(mock_extract.await_count, 2)
        self.assertEqual(state.animation_file_id, 'file123')

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock, return_value=None)
    async def test_gives_up_without_crashing(self, mock_extract, mock_sleep):
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        await populate_animation(bot, state)
        self.assertEqual(mock_extract.await_count, 5)
        self.assertIsNone(state.animation_file_id)
        bot.send_video.assert_not_awaited()

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.extract_info', new_callable=AsyncMock)
    async def test_send_video_error_is_retried(self, mock_extract, mock_sleep):
        mock_extract.return_value = make_video_info()
        state = make_state(animation_file_id=None, config=make_config(loading_video_id='https://example.com/v'))
        bot = AsyncMock()
        bot.send_video.side_effect = Exception('network error')
        await populate_animation(bot, state)
        self.assertEqual(bot.send_video.await_count, 5)
        self.assertIsNone(state.animation_file_id)


class TestPopulateVideo(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.append_intent', new_callable=AsyncMock)
    async def test_returns_cached_video(self, mock_append):
        info = make_video_info(file_id='cached')
        state = make_state(videos={'url1': info})
        result = await populate_video('url1', ['100'], state)
        self.assertIs(result, info)
        mock_append.assert_not_awaited()

    @patch('dasovbot.services.background.append_intent', new_callable=AsyncMock)
    async def test_appends_intent_when_not_cached(self, mock_append):
        state = make_state(videos={})
        await populate_video('url1', ['100'], state, title='t', upload_date='20260101')
        mock_append.assert_awaited_once()
        self.assertEqual(mock_append.call_args[0][0], 'url1')
        self.assertEqual(mock_append.call_args[1]['chat_ids'], ['100'])


class TestPopulatePlaylist(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.populate_video', new_callable=AsyncMock)
    @patch('dasovbot.services.background.get_ydl')
    async def test_extraction_error_returns(self, mock_get_ydl, mock_populate):
        ydl = MagicMock()
        ydl.extract_info.side_effect = Exception('network error')
        mock_get_ydl.return_value = ydl
        await populate_playlist('channel', ['100'], make_state())
        mock_populate.assert_not_awaited()

    @patch('dasovbot.services.background.populate_video', new_callable=AsyncMock)
    @patch('dasovbot.services.background.get_ydl')
    async def test_no_entries_returns(self, mock_get_ydl, mock_populate):
        ydl = MagicMock()
        ydl.extract_info.return_value = {'entries': []}
        mock_get_ydl.return_value = ydl
        await populate_playlist('channel', ['100'], make_state())
        mock_populate.assert_not_awaited()

    @patch('dasovbot.services.background.populate_video', new_callable=AsyncMock)
    @patch('dasovbot.services.background.get_ydl')
    async def test_populates_latest_entries_oldest_first(self, mock_get_ydl, mock_populate):
        entries = [
            {'url': f'https://example.com/v{i}', 'title': f't{i}', 'duration': 10, 'upload_date': '20260101'}
            for i in range(7)
        ]
        ydl = MagicMock()
        ydl.extract_info.return_value = {'entries': entries}
        mock_get_ydl.return_value = ydl
        await populate_playlist('channel', ['100'], make_state())
        self.assertEqual(mock_populate.await_count, 5)
        first_populated = mock_populate.await_args_list[0][0][0]
        self.assertEqual(first_populated, 'https://example.com/v4')


class TestRunPopulateSubscriptions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The module-level lock binds to the first event loop that acquires it;
        # each test runs in a fresh loop, so give each test a fresh lock.
        import dasovbot.services.background as background
        background._populate_lock = asyncio.Lock()

    @patch('dasovbot.services.background.populate_playlist', new_callable=AsyncMock)
    async def test_skips_when_already_running(self, mock_populate):
        import dasovbot.services.background as background
        sub = Subscription(chat_ids=['100'], title='a', uploader='u', uploader_videos='url1/videos')
        state = make_state(subscriptions={'url1': sub})
        await background._populate_lock.acquire()
        try:
            await run_populate_subscriptions(state)
        finally:
            background._populate_lock.release()
        mock_populate.assert_not_awaited()
        self.assertNotIn('populate_subscriptions', state.background_task_status)

    @patch('dasovbot.services.background.populate_playlist', new_callable=AsyncMock)
    async def test_populates_subscribed_and_pops_empty(self, mock_populate):
        with_subs = Subscription(chat_ids=['100'], title='a', uploader='u', uploader_videos='url1/videos')
        without_subs = Subscription(chat_ids=[], title='b', uploader='u', uploader_videos='url2/videos')
        state = make_state(subscriptions={'url1': with_subs, 'url2': without_subs})
        state.pop_subscription = AsyncMock()
        await run_populate_subscriptions(state)
        mock_populate.assert_awaited_once_with('url1', ['100'], state)
        state.pop_subscription.assert_awaited_once_with('url2')
        self.assertIn('populate_subscriptions', state.background_task_status)


class TestRunForever(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    async def test_restarts_after_crash(self, mock_sleep):
        factory = AsyncMock(side_effect=[ValueError('boom'), asyncio.CancelledError()])
        with self.assertRaises(asyncio.CancelledError):
            await run_forever(factory, 'task1')
        self.assertEqual(factory.await_count, 2)
        mock_sleep.assert_awaited_once_with(RESTART_DELAY_SEC)

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    async def test_returns_when_factory_completes(self, mock_sleep):
        factory = AsyncMock(return_value=None)
        await run_forever(factory, 'task1')
        factory.assert_awaited_once()
        mock_sleep.assert_not_awaited()


class TestStartBackgroundTasks(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.monitor_backups', new_callable=AsyncMock)
    @patch('dasovbot.services.intent_processor.monitor_process_intents', new_callable=AsyncMock)
    @patch('dasovbot.services.background.clear_temporary_inline_queries', new_callable=AsyncMock)
    @patch('dasovbot.services.background.populate_subscriptions', new_callable=AsyncMock)
    @patch('dasovbot.services.background.populate_animation', new_callable=AsyncMock)
    async def test_keeps_strong_references_until_done(self, *mocks):
        state = make_state()
        start_background_tasks(AsyncMock(), state)
        self.assertEqual(len(state.background_tasks), 5)
        tasks = list(state.background_tasks)
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)  # let done callbacks run
        self.assertEqual(len(state.background_tasks), 0)


class TestNewestBackupAge(unittest.TestCase):
    def test_returns_none_without_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(newest_backup_age(tmp))

    def test_returns_nonnegative_age_of_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'bot.db.backup_20260101_000000'), 'w') as f:
                f.write('x')
            age = newest_backup_age(tmp)
            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, 0)


class TestMonitorBackups(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.newest_backup_age', return_value=None)
    async def test_alerts_developer_when_no_backups(self, mock_age, mock_sleep):
        mock_sleep.side_effect = asyncio.CancelledError()
        state = make_state(config=make_config(developer_chat_id='999'))
        bot = AsyncMock()
        with self.assertRaises(asyncio.CancelledError):
            await monitor_backups(bot, state)
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs['chat_id'], '999')
        self.assertIn('monitor_backups', state.background_task_status)

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.newest_backup_age', return_value=100)
    async def test_no_alert_when_backup_fresh(self, mock_age, mock_sleep):
        mock_sleep.side_effect = asyncio.CancelledError()
        state = make_state(config=make_config())
        bot = AsyncMock()
        with self.assertRaises(asyncio.CancelledError):
            await monitor_backups(bot, state)
        bot.send_message.assert_not_awaited()

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    async def test_alerts_once_while_still_stale(self, mock_sleep):
        from dasovbot.constants import BACKUP_STALE_SEC
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        state = make_state(config=make_config())
        bot = AsyncMock()
        with patch('dasovbot.services.background.newest_backup_age', return_value=BACKUP_STALE_SEC + 1):
            with self.assertRaises(asyncio.CancelledError):
                await monitor_backups(bot, state)
        bot.send_message.assert_awaited_once()

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.newest_backup_age', return_value=None)
    async def test_sets_health_alert_when_stale(self, mock_age, mock_sleep):
        mock_sleep.side_effect = asyncio.CancelledError()
        state = make_state(config=make_config())
        with self.assertRaises(asyncio.CancelledError):
            await monitor_backups(AsyncMock(), state)
        self.assertIn('backup_stale', state.health_alerts)
        self.assertEqual(state.health_alerts['backup_stale']['level'], 'warning')

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.newest_backup_age', return_value=100)
    async def test_clears_health_alert_when_fresh(self, mock_age, mock_sleep):
        mock_sleep.side_effect = asyncio.CancelledError()
        state = make_state(config=make_config())
        state.set_alert('backup_stale', 'stale', level='warning')
        with self.assertRaises(asyncio.CancelledError):
            await monitor_backups(AsyncMock(), state)
        self.assertNotIn('backup_stale', state.health_alerts)

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    async def test_send_failure_not_latched(self, mock_sleep):
        from dasovbot.constants import BACKUP_STALE_SEC
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        state = make_state(config=make_config())
        bot = AsyncMock()
        bot.send_message.side_effect = Exception('network error')
        with patch('dasovbot.services.background.newest_backup_age', return_value=BACKUP_STALE_SEC + 1):
            with self.assertRaises(asyncio.CancelledError):
                await monitor_backups(bot, state)
        # A failed alert must retry on the next cycle rather than latch silent
        self.assertEqual(bot.send_message.await_count, 2)


class TestStopBackgroundTasks(unittest.IsolatedAsyncioTestCase):
    async def test_cancels_running_tasks(self):
        state = make_state()

        async def forever():
            await asyncio.Event().wait()

        task = asyncio.create_task(forever())
        state.background_tasks.add(task)
        await stop_background_tasks(state)
        self.assertTrue(task.cancelled())

    async def test_noop_without_tasks(self):
        state = make_state()
        await stop_background_tasks(state)
        self.assertEqual(state.background_tasks, set())


class TestClearTemporaryInlineQueries(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    async def test_marks_then_deletes_on_second_pass(self, mock_sleep):
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        fresh = TemporaryInlineQuery(marked=False)
        stale = TemporaryInlineQuery(marked=True)
        state = make_state(temporary_inline_queries={'fresh': fresh, 'stale': stale})
        with self.assertRaises(asyncio.CancelledError):
            await clear_temporary_inline_queries(state)
        # After two passes: 'stale' deleted in the first, 'fresh' marked then deleted in the second
        self.assertEqual(state.temporary_inline_queries, {})
        self.assertIn('clear_temporary_inline_queries', state.background_task_status)

    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    async def test_ignored_entries_survive_sweep(self, mock_sleep):
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        ignored = TemporaryInlineQuery(ignored=True, marked=True)
        state = make_state(temporary_inline_queries={'ignored': ignored})
        with self.assertRaises(asyncio.CancelledError):
            await clear_temporary_inline_queries(state)
        self.assertIn('ignored', state.temporary_inline_queries)
        self.assertTrue(ignored.ignored)


class TestPopulateSubscriptionsLoop(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    @patch('dasovbot.services.background.run_populate_subscriptions', new_callable=AsyncMock)
    async def test_runs_then_sleeps_for_interval(self, mock_run, mock_sleep):
        from dasovbot.constants import INTERVAL_SEC
        mock_sleep.side_effect = asyncio.CancelledError()
        state = make_state()
        with self.assertRaises(asyncio.CancelledError):
            await populate_subscriptions(state)
        mock_run.assert_awaited_once_with(state)
        mock_sleep.assert_awaited_once_with(INTERVAL_SEC)


class TestRunPopulateSubscriptionsVanished(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.populate_playlist', new_callable=AsyncMock)
    async def test_skips_subscription_removed_mid_iteration(self, mock_populate):
        state = make_state(subscriptions={'url': None})
        await run_populate_subscriptions(state)
        mock_populate.assert_not_awaited()
        self.assertIn('populate_subscriptions', state.background_task_status)


class TestClearTemporaryInlineQueriesVanished(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.services.background.asyncio.sleep', new_callable=AsyncMock)
    async def test_skips_entry_removed_mid_iteration(self, mock_sleep):
        mock_sleep.side_effect = asyncio.CancelledError()
        state = make_state(temporary_inline_queries={'url': None})
        with self.assertRaises(asyncio.CancelledError):
            await clear_temporary_inline_queries(state)
        self.assertIn('url', state.temporary_inline_queries)


class TestOnTaskDone(unittest.IsolatedAsyncioTestCase):
    async def test_logs_failed_task_and_discards_reference(self):
        state = make_state()

        async def boom():
            raise RuntimeError('task failed')

        task = asyncio.create_task(boom())
        state.background_tasks.add(task)
        from functools import partial
        task.add_done_callback(partial(_on_task_done, state))
        with self.assertLogs('dasovbot.services.background', level='ERROR'):
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
        self.assertNotIn(task, state.background_tasks)

    async def test_cancelled_task_not_logged_as_error(self):
        state = make_state()

        async def forever():
            await asyncio.sleep(3600)

        task = asyncio.create_task(forever())
        state.background_tasks.add(task)
        from functools import partial
        task.add_done_callback(partial(_on_task_done, state))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertNotIn(task, state.background_tasks)


if __name__ == '__main__':
    unittest.main()
