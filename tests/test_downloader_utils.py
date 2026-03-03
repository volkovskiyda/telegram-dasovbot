import unittest
from unittest.mock import patch

from dasovbot.downloader import (
    extract_url, process_info, contains_text,
    filter_entries, process_entries,
)
from dasovbot.models import VideoInfo


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

    def test_description_truncation(self):
        raw = {
            'title': 'T',
            'url': 'https://x.com',
            'description': 'x' * 2000,
        }
        result = process_info(raw)
        self.assertEqual(len(result.description), 1000)

    def test_no_description(self):
        raw = {'title': 'T', 'url': 'https://x.com'}
        result = process_info(raw)
        self.assertEqual(result.description, '')


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
