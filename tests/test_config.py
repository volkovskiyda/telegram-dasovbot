import unittest
from unittest.mock import patch

from dasovbot.config import load_config, match_filter, make_ydl_opts
from tests.helpers import make_config


@patch('dasovbot.config.dotenv.load_dotenv')
class TestLoadConfig(unittest.TestCase):
    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'https://api.telegram.org',
        'DEVELOPER_CHAT_ID': '123',
    }, clear=True)
    def test_loads_required_vars(self, mock_dotenv):
        config = load_config()
        self.assertEqual(config.bot_token, 'tok')
        self.assertEqual(config.base_url, 'https://api.telegram.org')
        self.assertEqual(config.developer_chat_id, '123')

    @patch.dict('os.environ', {
        'BASE_URL': 'https://api.telegram.org',
        'DEVELOPER_CHAT_ID': '123',
    }, clear=True)
    def test_raises_on_missing_bot_token(self, mock_dotenv):
        with self.assertRaises(ValueError) as ctx:
            load_config()
        self.assertIn('BOT_TOKEN', str(ctx.exception))

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'DEVELOPER_CHAT_ID': '123',
    }, clear=True)
    def test_raises_on_missing_base_url(self, mock_dotenv):
        with self.assertRaises(ValueError):
            load_config()

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'https://api.telegram.org',
    }, clear=True)
    def test_raises_on_missing_developer_chat_id(self, mock_dotenv):
        with self.assertRaises(ValueError):
            load_config()

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'https://api.telegram.org',
        'DEVELOPER_CHAT_ID': '123',
    }, clear=True)
    def test_developer_id_defaults_to_developer_chat_id(self, mock_dotenv):
        config = load_config()
        self.assertEqual(config.developer_id, '123')

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'https://api.telegram.org',
        'DEVELOPER_CHAT_ID': '123',
        'DEVELOPER_ID': '456',
    }, clear=True)
    def test_developer_id_override(self, mock_dotenv):
        config = load_config()
        self.assertEqual(config.developer_id, '456')

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'https://api.telegram.org',
        'DEVELOPER_CHAT_ID': '123',
        'READ_TIMEOUT': '60',
    }, clear=True)
    def test_read_timeout(self, mock_dotenv):
        config = load_config()
        self.assertEqual(config.read_timeout, 60.0)

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'https://api.telegram.org',
        'DEVELOPER_CHAT_ID': '123',
        'EMPTY_MEDIA_FOLDER': 'true',
    }, clear=True)
    def test_empty_media_folder(self, mock_dotenv):
        config = load_config()
        self.assertTrue(config.empty_media_folder)

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'https://api.telegram.org',
        'DEVELOPER_CHAT_ID': '123',
        'COOKIES_FILE': '/path/cookies.txt',
    }, clear=True)
    def test_cookies_file(self, mock_dotenv):
        config = load_config()
        self.assertEqual(config.cookies_file, '/path/cookies.txt')


class TestMatchFilter(unittest.TestCase):
    def test_normal_video(self):
        info = {'duration': 120, 'is_live': False, 'url': 'https://example.com'}
        result = match_filter(info, incomplete=False)
        self.assertIsNone(result)

    def test_live_video(self):
        info = {'duration': 120, 'is_live': True, 'url': 'https://example.com'}
        result = match_filter(info, incomplete=False)
        self.assertIsNotNone(result)
        self.assertIn('ignore_video', result)

    def test_long_video(self):
        info = {'duration': 20000, 'is_live': False, 'url': 'https://example.com'}
        result = match_filter(info, incomplete=False)
        self.assertIsNotNone(result)

    def test_no_duration(self):
        info = {'is_live': False, 'url': 'https://example.com'}
        result = match_filter(info, incomplete=False)
        self.assertIsNone(result)


class TestMakeYdlOpts(unittest.TestCase):
    def test_expected_keys(self):
        config = make_config()
        opts = make_ydl_opts(config)
        self.assertIn('format', opts)
        self.assertIn('outtmpl', opts)
        self.assertIn('retries', opts)
        self.assertIn('match_filter', opts)
        self.assertIn('merge_output_format', opts)

    def test_cookies_conditional(self):
        config = make_config(cookies_file='')
        opts = make_ydl_opts(config)
        self.assertNotIn('cookiefile', opts)

        config_with_cookies = make_config(cookies_file='/path/cookies.txt')
        opts_with = make_ydl_opts(config_with_cookies)
        self.assertEqual(opts_with['cookiefile'], '/path/cookies.txt')

    def test_media_folder_in_outtmpl(self):
        config = make_config(config_folder='/myconfig')
        opts = make_ydl_opts(config)
        self.assertIn('/myconfig/media/', opts['outtmpl'])


if __name__ == '__main__':
    unittest.main()
