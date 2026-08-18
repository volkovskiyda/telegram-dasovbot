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
"""
import asyncio
import glob
import os
import unittest
from unittest.mock import MagicMock

from dasovbot.constants import SOURCE_SUBSCRIPTION, SUBSCRIBE_SHOW
from dasovbot.downloader import get_ydl
from dasovbot.models import Subscription
from dasovbot.services.background import run_populate_subscriptions
from dasovbot.services.intent_processor import append_intent, process_query
from tests.integration.base import IntegrationTestBase


def _requires_real_video(test_config):
    url = test_config.test_video_url
    if not url or 'example.com' in url:
        raise unittest.SkipTest('TEST_VIDEO_URL must be a real video URL (not example.com)')


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


class TestIntentPipelineDownload(RealPipelineTestBase):
    """Full intent pipeline: intent -> real download -> real Telegram upload ->
    delivery to requesters -> file_id cached. Gated: actually downloads
    TEST_VIDEO_URL (keep it a short clip) and posts it to the test chat."""

    def setUp(self):
        super().setUp()
        if not os.getenv('TEST_ENABLE_DOWNLOAD'):
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


if __name__ == '__main__':
    unittest.main()
