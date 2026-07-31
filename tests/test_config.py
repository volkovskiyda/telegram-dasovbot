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
    }, clear=True)
    def test_config_folder_defaults_to_relative_folder(self, mock_dotenv):
        # Must not default to '/', which would write data/media to the fs root.
        config = load_config()
        self.assertEqual(config.config_folder, './config')

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

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'http://localhost:8081/bot',
        'DEVELOPER_CHAT_ID': '123',
    }, clear=True)
    def test_local_mode_defaults_false(self, mock_dotenv):
        # Off by default: enabling it against a server without --local breaks
        # every upload ("wrong file identifier" for file:// URIs)
        config = load_config()
        self.assertFalse(config.local_mode)

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'http://localhost:8081/bot',
        'DEVELOPER_CHAT_ID': '123',
        'LOCAL_MODE': 'true',
    }, clear=True)
    def test_local_mode_enabled(self, mock_dotenv):
        config = load_config()
        self.assertTrue(config.local_mode)

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'http://localhost:8081/bot',
        'DEVELOPER_CHAT_ID': '123',
    }, clear=True)
    def test_base_file_url_derived_from_base_url(self, mock_dotenv):
        config = load_config()
        self.assertEqual(config.base_file_url, 'http://localhost:8081/file/bot')

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'http://localhost:8081/bot',
        'DEVELOPER_CHAT_ID': '123',
        'BASE_FILE_URL': 'http://files:9000/file/bot',
    }, clear=True)
    def test_base_file_url_env_override(self, mock_dotenv):
        config = load_config()
        self.assertEqual(config.base_file_url, 'http://files:9000/file/bot')

    @patch.dict('os.environ', {
        'BOT_TOKEN': 'tok',
        'BASE_URL': 'https://api.telegram.org',
        'DEVELOPER_CHAT_ID': '123',
    }, clear=True)
    def test_base_file_url_empty_without_bot_suffix(self, mock_dotenv):
        # No '/bot' suffix to map: keep '' so the builder falls back to the
        # library default instead of guessing a wrong endpoint
        config = load_config()
        self.assertEqual(config.base_file_url, '')


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
