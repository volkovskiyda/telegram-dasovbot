"""
Integration tests for real subscription, playlist, and download pipelines.

These run real yt-dlp extraction against YouTube and real Telegram API calls.
They are deliberately light on the network: subscription and playlist tests
fetch flat playlist metadata only (one page per extraction, no video data),
and the only actual video download is the short TEST_VIDEO_URL clip, gated
behind TEST_ENABLE_DOWNLOAD=1.

Requires .env.test with TEST_BOT_TOKEN, TEST_USER_ID, TEST_CHAT_ID and a real
TEST_VIDEO_URL (a short video — it is downloaded and uploaded for real).
Optional TEST_CHANNEL_URL sets the channel used for subscription tests;
without it, the channel that owns TEST_VIDEO_URL is used.
Optional TEST_PLAYLIST_URLS (comma-separated) enables the real-playlist
subscription tests; the gated playlist download test only tries the
shortest entries, and skips entirely if nothing is under 5 minutes.
"""
import asyncio
import glob
import os
import unittest
from unittest.mock import MagicMock

from dasovbot.constants import SOURCE_SUBSCRIPTION, SUBSCRIBE_SHOW
from dasovbot.downloader import extract_url, filter_entries, get_ydl
from dasovbot.models import Subscription
from dasovbot.services.background import populate_playlist, run_populate_subscriptions
from dasovbot.services.intent_processor import append_intent, process_query
from tests.integration.base import IntegrationTestBase


def _requires_real_video(test_config):
    url = test_config.test_video_url
    if not url or 'example.com' in url:
        raise unittest.SkipTest('TEST_VIDEO_URL must be a real video URL (not example.com)')


def _requires_playlists(test_config):
    if not test_config.playlist_urls:
        raise unittest.SkipTest(
            'TEST_PLAYLIST_URLS not set in .env.test (comma-separated playlist URLs)')


_channel_cache: dict[str, str] = {}


async def _channel_url(test_config) -> str:
    """Channel for subscription tests: TEST_CHANNEL_URL, or derived once from
    TEST_VIDEO_URL's metadata and cached for the rest of the run."""
    override = os.getenv('TEST_CHANNEL_URL')
    if override:
        return override.rstrip('/')
    url = test_config.test_video_url
    if url not in _channel_cache:
        loop = asyncio.get_running_loop()

        def _extract():
            ydl = get_ydl()
            try:
                return ydl.extract_info(url, download=False)
            finally:
                ydl.close()

        raw = await loop.run_in_executor(None, _extract)
        # Prefer uploader_url (the @handle form): the subscribe handlers key
        # subscriptions by the uploader_url yt-dlp resolves, not the UC… id
        channel = (raw.get('uploader_url') or raw.get('channel_url') or '').rstrip('/')
        if not channel:
            raise unittest.SkipTest(
                'Cannot derive a channel from TEST_VIDEO_URL; set TEST_CHANNEL_URL')
        _channel_cache[url] = channel
    return _channel_cache[url]


class RealPipelineTestBase(IntegrationTestBase):
    def setUp(self):
        _requires_real_video(self.test_config)

    def make_context(self, user_data=None):
        """Minimal context for calling handlers directly (bypassing routing),
        so their return values and user_data effects can be asserted."""
        context = MagicMock()
        context.bot = self.bot
        context.bot_data = self.application.bot_data
        context.user_data = user_data if user_data is not None else {}
        return context

    def make_bound_update(self, text):
        """An update whose message can really reply through the test bot."""
        update = self.make_update(text)
        update.message.set_bot(self.bot)
        return update


class TestRealSubscriptionPolling(RealPipelineTestBase):
    """Subscription polling against a real channel: flat metadata extraction
    creating intents, without downloading anything."""

    async def test_populate_creates_intents_from_real_channel(self):
        channel_videos = f"{await _channel_url(self.test_config)}/videos"
        chat_id = str(self.test_config.chat_id)
        self.state.subscriptions[channel_videos] = Subscription(
            chat_ids=[chat_id], title='Test Channel', uploader='uploader',
            uploader_videos=channel_videos,
        )

        await run_populate_subscriptions(self.state)

        self.assertTrue(self.state.intents, 'polling a real channel created no intents')
        self.assertLessEqual(len(self.state.intents), 5)
        for query, intent in self.state.intents.items():
            self.assertTrue(query.startswith('http'), query)
            self.assertEqual(intent.chat_ids, [chat_id])
            self.assertEqual(intent.source, SOURCE_SUBSCRIPTION)
            self.assertTrue(intent.title, f'intent has no title: {query}')
        # The worker queue was signalled for the new intents
        self.assertFalse(self.state.download_queue.empty())
        self.assertIn('populate_subscriptions', self.state.background_task_status)

    async def test_populate_skips_already_cached_videos(self):
        from dasovbot.models import VideoInfo
        channel_videos = f"{await _channel_url(self.test_config)}/videos"
        chat_id = str(self.test_config.chat_id)
        self.state.subscriptions[channel_videos] = Subscription(
            chat_ids=[chat_id], title='Test Channel', uploader='uploader',
            uploader_videos=channel_videos,
        )

        await run_populate_subscriptions(self.state)
        first_queries = set(self.state.intents)

        # Mark every discovered video as already uploaded, re-poll: no new
        # intents may appear and the cached ones must not be re-requested
        self.state.intents.clear()
        for query in first_queries:
            self.state.videos[query] = VideoInfo(title='cached', file_id='cached-file-id')

        await run_populate_subscriptions(self.state)

        self.assertEqual(self.state.intents, {})


class TestRealSubscribeCommand(RealPipelineTestBase):
    """Subscribe handlers with real channel extraction and real bot replies."""

    async def test_subscribe_url_offers_playlists(self):
        from dasovbot.handlers.subscription import subscribe_url
        channel = await _channel_url(self.test_config)
        update = self.make_bound_update(f'/subscribe {channel}')
        context = self.make_context()

        result = await subscribe_url(update, context)

        # A channel with a playlists tab lands in the picker; one without is
        # subscribed to its /videos playlist directly
        if result == SUBSCRIBE_SHOW:
            chat_id = str(self.test_config.chat_id)
            videos_subs = [sub for url, sub in self.state.subscriptions.items()
                           if url.endswith('/videos') and chat_id in sub.chat_ids]
            self.assertTrue(videos_subs, 'direct subscribe created no /videos subscription')
            self.assertTrue(videos_subs[0].uploader)
        else:
            from dasovbot.constants import SUBSCRIBE_PLAYLIST
            self.assertEqual(result, SUBSCRIBE_PLAYLIST)
            self.assertTrue(context.user_data.get('playlists'))

    async def test_subscribe_playlist_url_creates_subscription(self):
        from dasovbot.handlers.subscription import subscribe_playlist
        channel_videos = f"{await _channel_url(self.test_config)}/videos"
        chat_id = str(self.test_config.chat_id)
        update = self.make_bound_update(channel_videos)
        context = self.make_context()

        result = await subscribe_playlist(update, context)

        self.assertEqual(result, SUBSCRIBE_SHOW)
        subscription = self.state.subscriptions.get(channel_videos)
        self.assertIsNotNone(subscription, 'subscription was not created')
        self.assertIn(chat_id, subscription.chat_ids)
        self.assertTrue(subscription.title)
        self.assertTrue(subscription.uploader_videos)
        self.assertEqual(context.user_data.get('subscription_url'), channel_videos)


class TestRealPlaylistsCommand(RealPipelineTestBase):
    """/playlists with a real subscription: real uploader_url extraction and
    the real probe of the complementary videos/streams tab."""

    async def test_playlists_with_real_subscription(self):
        from telegram.ext import ConversationHandler
        from dasovbot.handlers.subscription import playlists
        channel_videos = f"{await _channel_url(self.test_config)}/videos"
        chat_id = str(self.test_config.chat_id)
        self.state.subscriptions[channel_videos] = Subscription(
            chat_ids=[chat_id], title='Test Channel', uploader='uploader',
            uploader_videos=channel_videos,
        )
        update = self.make_bound_update('/playlists')
        context = self.make_context()

        result = await playlists(update, context)

        # Whether the channel has a streams tab decides if a suggestion is
        # sent; either way the handler must complete the conversation
        self.assertEqual(result, ConversationHandler.END)


class TestRealPlaylistSubscriptions(RealPipelineTestBase):
    """Subscription behaviour against the real playlists in TEST_PLAYLIST_URLS.
    Flat metadata extraction only — nothing is downloaded here."""

    def setUp(self):
        # Only needs playlists; TEST_VIDEO_URL is irrelevant for these tests
        _requires_playlists(self.test_config)

    async def test_populate_creates_intents_from_each_playlist(self):
        chat_id = str(self.test_config.chat_id)
        for playlist_url in self.test_config.playlist_urls:
            with self.subTest(playlist=playlist_url):
                self.state.intents.clear()
                await populate_playlist(playlist_url, [chat_id], self.state)

                self.assertTrue(self.state.intents,
                                f'polling {playlist_url} created no intents')
                self.assertLessEqual(len(self.state.intents), 5)
                for query, intent in self.state.intents.items():
                    self.assertTrue(query.startswith('http'), query)
                    self.assertIn(chat_id, intent.chat_ids)
                    self.assertEqual(intent.source, SOURCE_SUBSCRIPTION)
                    self.assertTrue(intent.title, f'intent has no title: {query}')
                self.assertFalse(self.state.download_queue.empty())

    async def test_populate_subscriptions_polls_all_playlists(self):
        chat_id = str(self.test_config.chat_id)
        for playlist_url in self.test_config.playlist_urls:
            self.state.subscriptions[playlist_url] = Subscription(
                chat_ids=[chat_id], title='Test Playlist', uploader='uploader',
                uploader_videos=playlist_url,
            )

        await run_populate_subscriptions(self.state)

        self.assertTrue(self.state.intents,
                        'polling the subscribed playlists created no intents')
        self.assertLessEqual(len(self.state.intents),
                             5 * len(self.test_config.playlist_urls))
        self.assertIn('populate_subscriptions', self.state.background_task_status)

    async def test_subscribe_playlist_creates_real_subscriptions(self):
        from dasovbot.handlers.subscription import subscribe_playlist
        chat_id = str(self.test_config.chat_id)
        for playlist_url in self.test_config.playlist_urls:
            with self.subTest(playlist=playlist_url):
                update = self.make_bound_update(playlist_url)
                context = self.make_context()

                result = await subscribe_playlist(update, context)

                self.assertEqual(result, SUBSCRIBE_SHOW)
                subscription = self.state.subscriptions.get(playlist_url)
                self.assertIsNotNone(
                    subscription, f'no subscription created for {playlist_url}')
                self.assertIn(chat_id, subscription.chat_ids)
                self.assertTrue(subscription.title)
                self.assertEqual(context.user_data.get('subscription_url'),
                                 playlist_url)


class RealDownloadTestBase(RealPipelineTestBase):
    """Shared setup for tests that really download and upload: gated behind
    TEST_ENABLE_DOWNLOAD, creates the media/export folders, cleans them up."""

    def setUp(self):
        if os.getenv('TEST_ENABLE_DOWNLOAD') != '1':
            self.skipTest('Download tests disabled. Set TEST_ENABLE_DOWNLOAD=1 to enable')

    async def asyncSetUp(self):
        await super().asyncSetUp()
        config = self.state.config
        os.makedirs(config.media_folder, exist_ok=True)
        # post_process exports developer-requested files to /export/
        os.makedirs(f'{config.config_folder}/export', exist_ok=True)

    async def asyncTearDown(self):
        for pattern in ('media', 'export'):
            for path in glob.glob(f'{self.state.config.config_folder}/{pattern}/*'):
                try:
                    os.remove(path)
                except OSError:
                    pass
        await super().asyncTearDown()


class TestIntentPipelineDownload(RealDownloadTestBase):
    """Full intent pipeline: intent -> real download -> real Telegram upload ->
    delivery to requesters -> file_id cached. Gated: actually downloads
    TEST_VIDEO_URL (keep it a short clip) and posts it to the test chat."""

    def setUp(self):
        _requires_real_video(self.test_config)
        super().setUp()

    async def test_intent_download_upload_and_delivery(self):
        url = self.test_config.test_video_url
        chat_id = str(self.test_config.chat_id)

        await append_intent(url, self.state, chat_ids=[chat_id], source='download')
        self.assertIn(url, self.state.intents)

        info = await process_query(self.bot, url, self.state)

        self.assertIsNotNone(info, 'process_query returned no info')
        cached = self.state.videos.get(url)
        self.assertIsNotNone(cached, 'video was not cached after processing')
        self.assertTrue(cached.file_id, 'no file_id captured from the upload')
        # Delivered and cleaned up: intent consumed, media file gone
        self.assertNotIn(url, self.state.intents)
        if info.filepath:
            self.assertFalse(os.path.exists(info.filepath),
                             f'downloaded file was not cleaned up: {info.filepath}')

        # Second request for the same video: served from cache, no download
        await append_intent(url, self.state, chat_ids=[chat_id], source='download')
        info2 = await process_query(self.bot, url, self.state)

        self.assertIsNotNone(info2)
        self.assertEqual(info2.file_id, cached.file_id)
        self.assertNotIn(url, self.state.intents)


class TestRealPlaylistIntentDownload(RealDownloadTestBase):
    """A subscription-sourced intent from a real playlist goes through the
    actual download/upload/delivery pipeline. ISP-safe: only entries up to
    MAX_DURATION_SEC qualify, tried shortest-first. YouTube blocks the data
    download of some individual videos (PO-token enforcement — extraction
    succeeds, the download 403s), so up to MAX_ATTEMPTS candidates are tried
    before the test fails."""

    MAX_DURATION_SEC = 300
    MAX_ATTEMPTS = 3

    def setUp(self):
        _requires_playlists(self.test_config)
        super().setUp()

    async def _short_entries(self) -> list[dict]:
        """All entries under MAX_DURATION_SEC across the playlists, shortest
        first, deduplicated by URL."""
        loop = asyncio.get_running_loop()
        entries: dict[str, dict] = {}
        for playlist_url in self.test_config.playlist_urls:
            def _extract(url=playlist_url):
                ydl = get_ydl()
                try:
                    return ydl.extract_info(url, download=False)
                finally:
                    ydl.close()

            info = await loop.run_in_executor(None, _extract)
            for entry in filter_entries(info.get('entries') or []):
                if entry.get('duration') and entry['duration'] <= self.MAX_DURATION_SEC:
                    entries.setdefault(extract_url(entry), entry)
        if not entries:
            self.skipTest(f'no playlist entry under {self.MAX_DURATION_SEC}s — '
                          'refusing to download a long video')
        return sorted(entries.values(), key=lambda entry: entry['duration'])

    async def test_subscription_intent_downloads_and_delivers(self):
        chat_id = str(self.test_config.chat_id)
        candidates = (await self._short_entries())[:self.MAX_ATTEMPTS]
        failures = []
        cached = info = url = None
        for entry in candidates:
            url = extract_url(entry)
            print(f"\n⏬ downloading playlist entry "
                  f"({entry['duration']}s): {entry.get('title')}")

            await append_intent(url, self.state, chat_ids=[chat_id],
                                source=SOURCE_SUBSCRIPTION, title=entry.get('title'),
                                upload_date=entry.get('upload_date'))
            self.assertIn(url, self.state.intents)

            # On a failed download process_query still returns the metadata
            # info (and keeps the intent for retry); real success is the
            # video cached with a file_id from the upload
            info = await process_query(self.bot, url, self.state)
            cached = self.state.videos.get(url)
            if info is not None and cached is not None and cached.file_id:
                break
            cached = None
            # Typically YouTube 403ing this video's data download; the next
            # candidate still proves the subscription download pipeline
            failures.append(f"{url} ({entry['duration']}s)")
            print(f'✗ download failed, trying next candidate: {url}')

        self.assertIsNotNone(
            cached,
            f'no candidate could be downloaded, tried: {failures}. If each '
            f'failed with a mid-download HTTP 403, YouTube is enforcing PO '
            f'tokens for these videos — the production bot would fail on '
            f'them too (see the [error_no_video_path] developer messages)')
        if failures:
            print(f'⚠ succeeded only after failed candidates: {failures}')
        self.assertTrue(cached.file_id, 'no file_id captured from the upload')
        self.assertNotIn(url, self.state.intents)
        if info.filepath:
            self.assertFalse(os.path.exists(info.filepath),
                             f'downloaded file was not cleaned up: {info.filepath}')


class TestInlineRealUploadE2E(RealDownloadTestBase):
    """Manual E2E for the inline flow with a real inline_message_id:

    1. You type `@<bot> <TEST_VIDEO_URL>` in the test chat and tap the result
    2. The real inline_query is answered with the loading placeholder
    3. Your chosen_inline_result queues an intent with the inline_message_id
    4. The pipeline downloads, uploads, and edits your inline message into
       the real video

    Requires ENABLE_E2E_TESTS=1, TEST_ENABLE_DOWNLOAD=1, and the bot to have
    inline mode AND inline feedback at 100% configured via @BotFather
    (/setinline, /setinlinefeedback) — without feedback no
    chosen_inline_result update ever arrives.
    """

    def setUp(self):
        _requires_real_video(self.test_config)
        if os.getenv('ENABLE_E2E_TESTS') != '1':
            self.skipTest('E2E tests disabled. Set ENABLE_E2E_TESTS=1 to enable')
        super().setUp()

    async def _seed_animation_file_id(self):
        """The inline handler can only answer an un-downloaded video when a
        loading-animation file_id exists; upload the clip once to get one."""
        from dasovbot.downloader import extract_info
        info = await extract_info(self.test_config.test_video_url,
                                  download=True, state=self.state)
        self.assertIsNotNone(info, 'could not download the seed clip')
        self.assertTrue(info.filepath and os.path.exists(info.filepath))
        message = await self.bot.send_video(
            chat_id=self.test_config.chat_id, video=info.filepath,
            disable_notification=True)
        self.state.animation_file_id = message.video.file_id
        await self.bot.delete_message(self.test_config.chat_id, message.message_id)

    async def test_inline_chosen_result_delivers_video(self):
        url = self.test_config.test_video_url
        user_id = self.test_config.user_id
        await self.clear_updates()
        await self._seed_animation_file_id()

        await self.bot.send_message(
            chat_id=self.test_config.chat_id,
            text=(f'🤖 Integration Test: in this chat, paste\n\n'
                  f'@{self.bot_user.username} {url}\n\n'
                  f'wait for the result to appear, then tap it. '
                  f'(2 minutes; paste the full line, do not type it gradually)'))
        print('\n⏳ Waiting for your inline query and result tap… (120s)')

        loop = asyncio.get_event_loop()
        deadline = loop.time() + 120
        answered = False
        chosen = None
        while loop.time() < deadline and chosen is None:
            updates = await self.bot.get_updates(timeout=5)
            for update in updates:
                if (update.inline_query
                        and update.inline_query.from_user.id == user_id):
                    await self.simulate_update(update)
                    answered = True
                    print('✓ inline query answered with placeholder')
                elif (update.chosen_inline_result
                        and update.chosen_inline_result.from_user.id == user_id):
                    chosen = update
            if updates:
                await self.bot.get_updates(offset=updates[-1].update_id + 1)

        self.assertTrue(answered, 'no inline query received — was the inline '
                                  'query typed in time?')
        self.assertIsNotNone(
            chosen,
            'no chosen_inline_result received — inline feedback must be '
            'enabled at 100% via @BotFather (/setinlinefeedback)')
        self.assertTrue(chosen.chosen_inline_result.inline_message_id,
                        'chosen result carries no inline_message_id — the '
                        'result needs an inline keyboard (loading button)')

        await self.simulate_update(chosen)
        intent = self.state.intents.get(url)
        self.assertIsNotNone(intent, 'chosen_query queued no intent')
        self.assertTrue(intent.inline_message_ids)
        print('✓ chosen result queued an inline intent, processing…')

        info = await process_query(self.bot, url, self.state)

        self.assertIsNotNone(info)
        cached = self.state.videos.get(url)
        self.assertIsNotNone(cached)
        self.assertTrue(cached.file_id)
        self.assertNotIn(url, self.state.intents)
        print('✓ inline message edited to the real video — check your chat')


class TestLocalModeIntentPipeline(TestIntentPipelineDownload):
    """The same intent pipeline with PTB local_mode: the upload reaches the
    Bot API server as a file:// path it reads from the shared media folder,
    so the video bytes never pass through the bot process.

    Requires TEST_LOCAL_MODE=1 with TEST_BASE_URL pointing at a Bot API
    server started with --local that mounts /tmp/test_config/media at the
    same absolute path (mirrors the production LOCAL_MODE contract).
    """

    def setUp(self):
        if os.getenv('TEST_LOCAL_MODE') != '1':
            self.skipTest('Set TEST_LOCAL_MODE=1 (needs a --local Bot API '
                          'server sharing /tmp/test_config/media)')
        super().setUp()

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.assertTrue(self.state.config.local_mode)
        self.assertTrue(self.test_config.base_url,
                        'local_mode requires the local Bot API server')


if __name__ == '__main__':
    unittest.main()
