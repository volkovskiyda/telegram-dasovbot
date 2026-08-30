import unittest
from unittest.mock import MagicMock, patch

from aiohttp.test_utils import AioHTTPTestCase

import dasovbot.dashboard.auth as auth_module
from dasovbot.dashboard.auth import check_api_token, get_api_token
from dasovbot.dashboard.server import create_app
from dasovbot.models import VideoInfo
from tests.helpers import make_state, make_config

TOKEN = 'test-api-token'
AUTH = {'Authorization': f'Bearer {TOKEN}'}


def make_video(video_id='abc123def45', **overrides):
    defaults = dict(
        title='Cat Video',
        file_id='f1',
        webpage_url=f'https://www.youtube.com/watch?v={video_id}',
        upload_date='20260101',
        duration=120,
        video_id=video_id,
        channel='Cats',
        channel_id='UCcats',
        tags=['cat'],
        categories=['Pets'],
        chapters=[{'start_time': 0.0, 'title': 'Intro'}],
        thumbnail_url=f'https://i.ytimg.com/vi/{video_id}/maxresdefault.webp',
        epoch=1750750807,
    )
    defaults.update(overrides)
    return VideoInfo(**defaults)


class DashboardApiTestCase(AioHTTPTestCase):
    def setUp(self):
        self._env_patcher = patch.dict('os.environ', {'API_TOKEN': TOKEN})
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        super().setUp()

    async def get_application(self):
        self.state = make_state(
            config=make_config(),
            migration_progress={'status': 'skipped', 'tables': {}, 'elapsed': 0.0},
        )
        return create_app(self.state)


class TestApiAuth(DashboardApiTestCase):
    async def test_rejects_missing_token(self):
        resp = await self.client.get('/api/videos')
        self.assertEqual(resp.status, 401)

    async def test_rejects_wrong_token(self):
        resp = await self.client.get('/api/videos', headers={'Authorization': 'Bearer wrong'})
        self.assertEqual(resp.status, 401)

    async def test_rejects_non_bearer_scheme(self):
        resp = await self.client.get('/api/videos', headers={'Authorization': f'Basic {TOKEN}'})
        self.assertEqual(resp.status, 401)

    @patch('dasovbot.dashboard.auth.check_token', return_value=True)
    async def test_dashboard_session_does_not_grant_api_access(self, mock_check):
        # A logged-in browser session must not double as API authorization
        resp = await self.client.get('/api/videos')
        self.assertEqual(resp.status, 401)

    async def test_accepts_valid_token(self):
        resp = await self.client.get('/api/videos', headers=AUTH)
        self.assertEqual(resp.status, 200)


class TestGeneratedApiToken(unittest.TestCase):
    def setUp(self):
        auth_module._generated_api_token = None

    def test_generates_stable_token_without_env(self):
        with patch.dict('os.environ', {}, clear=True):
            token = get_api_token()
            self.assertGreaterEqual(len(token), 32)
            self.assertEqual(get_api_token(), token)

    def test_check_accepts_generated_token(self):
        with patch.dict('os.environ', {}, clear=True):
            token = get_api_token()
            request = MagicMock()
            request.headers = {'Authorization': f'Bearer {token}'}
            self.assertTrue(check_api_token(request))


class TestApiVideos(DashboardApiTestCase):
    async def _get_json(self, path):
        resp = await self.client.get(path, headers=AUTH)
        self.assertEqual(resp.status, 200)
        return await resp.json()

    async def test_dedupes_query_and_url_keys(self):
        # post_process stores the same VideoInfo under the user query and the
        # canonical URL; the API must return it once
        video = make_video()
        self.state.videos = {'cat video': video, video.webpage_url: video}
        items = await self._get_json('/api/videos')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['id'], 'abc123def45')

    async def test_skips_unenriched_and_pending_rows(self):
        old = make_video('idold123456')
        old.video_id = None
        self.state.videos = {
            'old': old,
            'pending': make_video('idpending12', file_id=None),
            'good': make_video('idgood12345'),
        }
        items = await self._get_json('/api/videos')
        self.assertEqual([item['id'] for item in items], ['idgood12345'])

    async def test_entry_shape_matches_index_schema(self):
        video = make_video(description='Full description', exported=True)
        self.state.videos = {video.webpage_url: video}
        items = await self._get_json('/api/videos')
        entry = items[0]
        self.assertEqual(entry['channel'], 'Cats')
        self.assertEqual(entry['channelId'], 'UCcats')
        self.assertEqual(entry['uploadDate'], '20260101')
        self.assertEqual(entry['duration'], 120)
        self.assertEqual(entry['tags'], ['cat'])
        self.assertEqual(entry['categories'], ['Pets'])
        self.assertEqual(entry['description'], 'Full description')
        # Stored chapter start_time is renamed to the index schema's 'start'
        self.assertEqual(entry['chapters'], [{'start': 0.0, 'title': 'Intro'}])
        self.assertEqual(entry['fetchedAt'], 1750750807)
        self.assertTrue(entry['exported'])
        self.assertEqual(entry['thumbnail'], 'https://i.ytimg.com/vi/abc123def45/maxresdefault.webp')

    async def test_thumbnail_falls_back_to_hqdefault(self):
        video = make_video(thumbnail_url=None)
        self.state.videos = {video.webpage_url: video}
        items = await self._get_json('/api/videos')
        self.assertEqual(items[0]['thumbnail'], 'https://i.ytimg.com/vi/abc123def45/hqdefault.jpg')

    async def test_exported_filter(self):
        exported = make_video('idexported1', exported=True)
        kept = make_video('idprivate12', exported=False)
        self.state.videos = {exported.webpage_url: exported, kept.webpage_url: kept}
        items = await self._get_json('/api/videos?exported=true')
        self.assertEqual([item['id'] for item in items], ['idexported1'])
        items = await self._get_json('/api/videos?exported=false')
        self.assertEqual([item['id'] for item in items], ['idprivate12'])

    async def test_etag_roundtrip_returns_304(self):
        video = make_video()
        self.state.videos = {video.webpage_url: video}
        resp = await self.client.get('/api/videos', headers=AUTH)
        etag = resp.headers['ETag']
        resp = await self.client.get('/api/videos', headers={**AUTH, 'If-None-Match': etag})
        self.assertEqual(resp.status, 304)
        # A content change must invalidate the tag
        other = make_video('idother1234')
        self.state.videos[other.webpage_url] = other
        resp = await self.client.get('/api/videos', headers={**AUTH, 'If-None-Match': etag})
        self.assertEqual(resp.status, 200)


class TestApiVideo(DashboardApiTestCase):
    async def test_returns_single_entry(self):
        video = make_video()
        self.state.videos = {video.webpage_url: video}
        resp = await self.client.get('/api/videos/abc123def45', headers=AUTH)
        self.assertEqual(resp.status, 200)
        entry = await resp.json()
        self.assertEqual(entry['id'], 'abc123def45')
        self.assertEqual(entry['title'], 'Cat Video')

    async def test_unknown_id_returns_404(self):
        resp = await self.client.get('/api/videos/nosuchvideo', headers=AUTH)
        self.assertEqual(resp.status, 404)
