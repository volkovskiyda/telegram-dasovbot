import unittest
from dasovbot.models import VideoInfo, VideoOrigin, Intent, IntentMessage, Subscription, TemporaryInlineQuery


class TestVideoOrigin(unittest.TestCase):
    def test_round_trip(self):
        origin = VideoOrigin(width=1920, height=1080, format='mp4')
        d = origin.to_dict()
        restored = VideoOrigin.from_dict(d)
        self.assertEqual(origin, restored)

    def test_round_trip_none_values(self):
        origin = VideoOrigin()
        d = origin.to_dict()
        restored = VideoOrigin.from_dict(d)
        self.assertEqual(origin, restored)


class TestVideoInfo(unittest.TestCase):
    def test_round_trip_minimal(self):
        info = VideoInfo(title='Test Video')
        d = info.to_dict()
        restored = VideoInfo.from_dict(d)
        self.assertEqual(info, restored)

    def test_round_trip_full(self):
        info = VideoInfo(
            title='Test Video',
            description='A test description',
            file_id='abc123',
            webpage_url='https://example.com/video',
            upload_date='20240101',
            timestamp='20240101_120000',
            thumbnail='https://example.com/thumb.jpg',
            duration=120,
            uploader_url='https://example.com/user',
            width=1920,
            height=1080,
            caption='[20240101] Test Video\nhttps://example.com/video',
            url='https://example.com/video',
            filepath='/tmp/video.mp4',
            filename='video.mp4',
            format='mp4',
            entries=None,
            origin=VideoOrigin(width=3840, height=2160, format='webm'),
        )
        d = info.to_dict()
        restored = VideoInfo.from_dict(d)
        self.assertEqual(info, restored)

    def test_from_dict_missing_fields(self):
        d = {'title': 'Minimal'}
        info = VideoInfo.from_dict(d)
        self.assertEqual(info.title, 'Minimal')
        self.assertEqual(info.description, '')
        self.assertIsNone(info.file_id)
        self.assertEqual(info.duration, 0)

    def test_from_dict_empty(self):
        d = {}
        info = VideoInfo.from_dict(d)
        self.assertEqual(info.title, '')

    def test_origin_included_in_dict(self):
        info = VideoInfo(title='Test', origin=VideoOrigin(width=1920, height=1080))
        d = info.to_dict()
        self.assertIn('origin', d)
        self.assertEqual(d['origin']['width'], 1920)

    def test_origin_excluded_when_none(self):
        info = VideoInfo(title='Test')
        d = info.to_dict()
        self.assertNotIn('origin', d)


class TestIntentMessage(unittest.TestCase):
    def test_round_trip(self):
        msg = IntentMessage(chat='123', message='456')
        d = msg.to_dict()
        restored = IntentMessage.from_dict(d)
        self.assertEqual(msg, restored)


class TestIntent(unittest.TestCase):
    def test_round_trip(self):
        intent = Intent(
            chat_ids=['123', '456'],
            inline_message_ids=['abc'],
            messages=[IntentMessage(chat='123', message='789')],
            priority=5,
            ignored=False,
        )
        d = intent.to_dict()
        restored = Intent.from_dict(d)
        self.assertEqual(intent, restored)

    def test_round_trip_empty(self):
        intent = Intent()
        d = intent.to_dict()
        restored = Intent.from_dict(d)
        self.assertEqual(intent, restored)

    def test_from_dict_missing_fields(self):
        d = {}
        intent = Intent.from_dict(d)
        self.assertEqual(intent.chat_ids, [])
        self.assertEqual(intent.priority, 0)
        self.assertFalse(intent.ignored)


class TestSubscription(unittest.TestCase):
    def test_round_trip(self):
        sub = Subscription(
            chat_ids=['123'],
            title='Test Channel',
            uploader='TestUser',
            uploader_videos='https://example.com/user/videos',
        )
        d = sub.to_dict()
        restored = Subscription.from_dict(d)
        self.assertEqual(sub, restored)

    def test_round_trip_empty(self):
        sub = Subscription()
        d = sub.to_dict()
        restored = Subscription.from_dict(d)
        self.assertEqual(sub, restored)


class TestTemporaryInlineQuery(unittest.TestCase):
    def test_defaults(self):
        tiq = TemporaryInlineQuery()
        self.assertEqual(tiq.timestamp, '')
        self.assertEqual(tiq.results, [])
        self.assertEqual(tiq.inline_queries, {})
        self.assertFalse(tiq.marked)
        self.assertFalse(tiq.ignored)


if __name__ == '__main__':
    unittest.main()
