"""
Integration test for yt-dlp format selection with the bot's real options.

Runs a real YouTube extraction (metadata only, nothing is downloaded) and
checks that the format yt-dlp picks with make_ydl_opts() actually reaches
VIDEO_RES. This guards against a silent quality collapse: when the chosen
player client stops serving DASH streams (e.g. it starts requiring a PO
token), the trailing `b[ext=mp4]` fallback still succeeds and every video
quietly ships as 360p progressive format 18.

Needs network access to YouTube only; no bot token or .env.test required.
"""
import unittest

import yt_dlp

from dasovbot.config import Config, make_ydl_opts
from dasovbot.constants import VIDEO_RES

# Big Buck Bunny (public domain, 2160p60): stable, and every rung of the
# ladder up to and beyond VIDEO_RES is available
REFERENCE_VIDEO_URL = 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'


class TestFormatSelection(unittest.TestCase):

    def _opts(self) -> dict:
        config = Config(
            bot_token='test', base_url='', developer_chat_id='1',
            developer_id='1', read_timeout=30.0, config_folder='/tmp/test_config',
        )
        opts = make_ydl_opts(config)
        opts.update({'simulate': True, 'match_filter': None, 'no_warnings': False})
        return opts

    def test_selected_format_reaches_target_resolution(self):
        with yt_dlp.YoutubeDL(self._opts()) as ydl:
            info = ydl.extract_info(REFERENCE_VIDEO_URL, download=False)

        available = sorted({
            f.get('height') or 0 for f in info['formats']
            if f.get('vcodec') not in (None, 'none', 'images')
        })
        chosen = info.get('format')
        print(f'\nselected: {chosen} | available heights: {available}')

        self.assertGreaterEqual(
            info.get('height') or 0, VIDEO_RES,
            f'yt-dlp picked {chosen} while {available} were listed: the '
            f'configured player client is only serving the progressive '
            f'fallback, see the player_client note in make_ydl_opts()')
        self.assertTrue(
            info.get('requested_formats'),
            f'{chosen} is a progressive (single-file) format; a separate '
            f'video+audio DASH pair was expected')


if __name__ == '__main__':
    unittest.main()
