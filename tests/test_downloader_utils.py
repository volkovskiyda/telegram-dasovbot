import unittest
from unittest.mock import patch

from dasovbot.downloader import (
    extract_url, process_info, contains_text,
    filter_entries, process_entries,
)
from dasovbot.models import VideoInfo


class TestInitDownloader(unittest.TestCase):
    def test_get_ydl_uses_initialized_opts(self):
        from tests.helpers import make_config
        from dasovbot.downloader import init_downloader, get_ydl
        init_downloader(make_config())
        ydl = get_ydl()
        self.assertEqual(ydl.params.get('merge_output_format'), 'mp4')
        ydl2 = get_ydl()
        self.assertIsNot(ydl, ydl2)


class TestAddScaledPassthrough(unittest.TestCase):
    def test_non_string_values_unchanged(self):
        from dasovbot.downloader import add_scaled_after_title
        self.assertEqual(add_scaled_after_title(42), 42)
        self.assertEqual(add_scaled_after_title(None), None)


class TestExtractUrl(unittest.TestCase):
    def test_from_video_info_webpage_url(self):
        info = VideoInfo(title='T', webpage_url='https://www.youtube.com/watch?v=abc')
        self.assertEqual(extract_url(info), 'https://www.youtube.com/watch?v=abc')

    def test_from_video_info_fallback_to_url(self):
        info = VideoInfo(title='T', url='https://fallback.com')
        self.assertEqual(extract_url(info), 'https://fallback.com')

    def test_from_dict_webpage_url(self):
        info = {'webpage_url': 'https://www.youtube.com/watch?v=abc', 'url': 'https://other.com'}
        self.assertEqual(extract_url(info), 'https://www.youtube.com/watch?v=abc')

    def test_from_dict_fallback_to_url(self):
        info = {'url': 'https://fallback.com'}
        self.assertEqual(extract_url(info), 'https://fallback.com')


class TestProcessInfo(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(process_info(None))

    def test_empty_dict(self):
        self.assertIsNone(process_info({}))

    def test_video_info_passthrough(self):
        info = VideoInfo(title='Test')
        result = process_info(info)
        self.assertIs(result, info)

    def test_basic_dict_conversion(self):
        raw = {
            'title': 'My Video',
            'webpage_url': 'https://www.youtube.com/watch?v=abc',
            'url': 'https://cdn.com/video.mp4',
            'duration': 120,
            'upload_date': '20240101',
            'description': 'A video description',
            'width': 1920,
            'height': 1080,
        }
        result = process_info(raw)
        self.assertIsInstance(result, VideoInfo)
        self.assertEqual(result.title, 'My Video')
        self.assertEqual(result.webpage_url, 'https://www.youtube.com/watch?v=abc')
        self.assertEqual(result.duration, 120)
        self.assertEqual(result.width, 1920)

    def test_requested_downloads(self):
        raw = {
            'title': 'T',
            'url': 'https://x.com',
            'requested_downloads': [{'filepath': '/tmp/video.mp4', 'filename': 'video.mp4'}],
        }
        result = process_info(raw)
        self.assertEqual(result.filepath, '/tmp/video.mp4')
        self.assertEqual(result.filename, 'video.mp4')

    def test_youtube_thumbnail(self):
        raw = {
            'title': 'T',
            'url': 'https://youtube.com/watch?v=abc123',
            'id': 'abc123',
        }
        result = process_info(raw)
        self.assertEqual(result.thumbnail, 'https://i.ytimg.com/vi/abc123/default.jpg')

    def test_non_youtube_thumbnail(self):
        raw = {
            'title': 'T',
            'url': 'https://other.com/video',
            'thumbnail': 'https://other.com/thumb.jpg',
        }
        result = process_info(raw)
        self.assertEqual(result.thumbnail, 'https://other.com/thumb.jpg')

    def test_timestamp_conversion(self):
        raw = {
            'title': 'T',
            'url': 'https://x.com',
            'timestamp': 1704067200,  # 2024-01-01 00:00:00 UTC
        }
        result = process_info(raw)
        self.assertIsNotNone(result.timestamp)
        self.assertIn('2024', result.timestamp)

    def test_caption_format(self):
        raw = {
            'title': 'My Video Title',
            'url': 'https://example.com',
            'webpage_url': 'https://example.com',
            'upload_date': '20240101',
        }
        result = process_info(raw)
        self.assertIn('[20240101]', result.caption)
        self.assertIn('My Video Title', result.caption)
        self.assertIn('https://example.com', result.caption)

    def test_title_fallback_to_url(self):
        raw = {'url': 'https://example.com/video'}
        result = process_info(raw)
        self.assertEqual(result.title, 'https://example.com/video')

    def test_description_kept_full(self):
        # Full descriptions are served by the API; the inline handler
        # truncates at its call site instead
        raw = {
            'title': 'T',
            'url': 'https://x.com',
            'description': 'x' * 2000,
        }
        result = process_info(raw)
        self.assertEqual(len(result.description), 2000)

    def test_no_description(self):
        raw = {'title': 'T', 'url': 'https://x.com'}
        result = process_info(raw)
        self.assertEqual(result.description, '')

    def test_index_metadata_fields(self):
        raw = {
            'title': 'T',
            'url': 'https://youtube.com/watch?v=abc123',
            'id': 'abc123',
            'channel': 'My Channel',
            'channel_id': 'UCxyz',
            'tags': ['tag1', 'tag2'],
            'categories': ['Entertainment'],
            'chapters': [{'start_time': 0.0, 'end_time': 10.0, 'title': 'Intro'}],
            'thumbnail': 'https://i.ytimg.com/vi/abc123/maxresdefault.webp',
            'epoch': 1750750807,
        }
        result = process_info(raw)
        self.assertEqual(result.video_id, 'abc123')
        self.assertEqual(result.channel, 'My Channel')
        self.assertEqual(result.channel_id, 'UCxyz')
        self.assertEqual(result.tags, ['tag1', 'tag2'])
        self.assertEqual(result.categories, ['Entertainment'])
        # end_time is dropped: only start_time/title are consumed downstream
        self.assertEqual(result.chapters, [{'start_time': 0.0, 'title': 'Intro'}])
        self.assertEqual(result.thumbnail_url, 'https://i.ytimg.com/vi/abc123/maxresdefault.webp')
        self.assertEqual(result.epoch, 1750750807)
        # The Telegram-facing thumbnail stays pinned to the small jpg
        self.assertEqual(result.thumbnail, 'https://i.ytimg.com/vi/abc123/default.jpg')

    def test_channel_falls_back_to_uploader(self):
        raw = {
            'title': 'T',
            'url': 'https://x.com',
            'uploader': 'Uploader Name',
            'uploader_id': '@handle',
        }
        result = process_info(raw)
        self.assertEqual(result.channel, 'Uploader Name')
        self.assertEqual(result.channel_id, '@handle')

    def test_epoch_defaults_to_now(self):
        result = process_info({'title': 'T', 'url': 'https://x.com'})
        self.assertIsInstance(result.epoch, int)
        self.assertGreater(result.epoch, 0)


class TestCaptionWithoutUploadDate(unittest.TestCase):
    def test_no_none_prefix(self):
        info = process_info({'title': 'My Video', 'url': 'https://example.com/v'})
        self.assertEqual(info.caption, 'My Video\nhttps://example.com/v')
        self.assertNotIn('None', info.caption)


class TestContainsText(unittest.TestCase):
    def test_case_insensitive_match(self):
        self.assertTrue(contains_text('This Video is Unavailable', ['video is unavailable']))

    def test_not_found(self):
        self.assertFalse(contains_text('Hello World', ['goodbye']))

    def test_multiple_items(self):
        self.assertTrue(contains_text('Private video', ['public', 'Private']))

    def test_empty_list(self):
        self.assertFalse(contains_text('anything', []))


class TestFilterEntries(unittest.TestCase):
    def test_no_duration(self):
        entries = [{'title': 'No duration'}]
        self.assertEqual(filter_entries(entries), [])

    def test_live(self):
        entries = [{'duration': 100, 'live_status': 'is_live'}]
        self.assertEqual(filter_entries(entries), [])

    def test_subscriber_only(self):
        entries = [{'duration': 100, 'availability': 'subscriber_only'}]
        self.assertEqual(filter_entries(entries), [])

    def test_valid_kept(self):
        entries = [{'duration': 100, 'live_status': 'not_live', 'availability': 'public'}]
        result = filter_entries(entries)
        self.assertEqual(len(result), 1)

    def test_empty(self):
        self.assertEqual(filter_entries([]), [])

    def test_mixed(self):
        entries = [
            {'duration': 100},
            {'title': 'no dur'},
            {'duration': 200, 'live_status': 'is_live'},
            {'duration': 300, 'availability': 'subscriber_only'},
        ]
        result = filter_entries(entries)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['duration'], 100)


class TestProcessEntries(unittest.TestCase):
    def test_nested_entries_unwrap(self):
        inner = [{'duration': 100, 'title': 'A'}, {'duration': 200, 'title': 'B'}]
        entries = [{'entries': inner}]
        result = process_entries(entries)
        self.assertEqual(result, inner)

    def test_filter_fallback(self):
        entries = [{'duration': 100, 'title': 'A'}, {'title': 'B'}]
        result = process_entries(entries)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['title'], 'A')


if __name__ == '__main__':
    unittest.main()
