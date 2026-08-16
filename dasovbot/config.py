import os
from dataclasses import dataclass

import dotenv

from dasovbot.constants import DATETIME_FORMAT, DATE_FORMAT, VIDEO_FORMAT, VIDEO_RES


@dataclass
class Config:
    bot_token: str
    base_url: str
    developer_chat_id: str
    developer_id: str
    read_timeout: float = 30.0
    loading_video_id: str = ""
    animation_file_id: str = ""
    config_folder: str = "./config"
    empty_media_folder: bool = False
    cookies_file: str = ""
    # Only valid against a telegram-bot-api server started with --local:
    # uploads are then passed as file:// paths the server reads from disk
    local_mode: bool = False
    base_file_url: str = ""
    upload_concurrency: int = 1

    @property
    def video_info_file(self) -> str:
        return f'{self.config_folder}/data/videos.json'

    @property
    def user_info_file(self) -> str:
        return f'{self.config_folder}/data/users.json'

    @property
    def subscription_info_file(self) -> str:
        return f'{self.config_folder}/data/subscriptions.json'

    @property
    def intent_info_file(self) -> str:
        return f'{self.config_folder}/data/intents.json'

    @property
    def db_file(self) -> str:
        return f'{self.config_folder}/data/bot.db'

    @property
    def media_folder(self) -> str:
        return f'{self.config_folder}/media'


def derive_base_file_url(base_url: str) -> str:
    """Map a Bot API base URL onto its file-download endpoint.

    api.telegram.org and the local telegram-bot-api server both serve file
    downloads from '<host>/file/bot<token>', so '<host>/bot' maps to
    '<host>/file/bot'. Returns '' (keep the library default) when the URL
    doesn't end in '/bot' and no mapping is known.
    """
    if base_url and base_url.endswith('/bot'):
        return f"{base_url.removesuffix('/bot')}/file/bot"
    return ''


def load_config() -> Config:
    dotenv.load_dotenv()

    required_env = ['BOT_TOKEN', 'BASE_URL', 'DEVELOPER_CHAT_ID']
    missing = [var for var in required_env if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    developer_chat_id = os.getenv('DEVELOPER_CHAT_ID')
    config_folder = os.getenv('CONFIG_FOLDER') or './config'
    base_url = os.getenv('BASE_URL')

    return Config(
        bot_token=os.getenv('BOT_TOKEN'),
        base_url=base_url,
        developer_chat_id=developer_chat_id,
        developer_id=os.getenv('DEVELOPER_ID') or developer_chat_id,
        read_timeout=float(os.getenv('READ_TIMEOUT') or 30),
        loading_video_id=os.getenv('LOADING_VIDEO_ID') or '',
        animation_file_id=os.getenv('ANIMATION_FILE_ID') or '',
        config_folder=config_folder,
        empty_media_folder=os.getenv('EMPTY_MEDIA_FOLDER', 'false').lower() == 'true',
        cookies_file=os.getenv('COOKIES_FILE') or '',
        local_mode=os.getenv('LOCAL_MODE', 'false').lower() == 'true',
        base_file_url=os.getenv('BASE_FILE_URL') or derive_base_file_url(base_url),
        # Floor of 1: a value of 0 would make the upload semaphore block forever
        upload_concurrency=max(1, int(os.getenv('UPLOAD_CONCURRENCY') or 1)),
    )


def match_filter(info, *, incomplete):
    from dasovbot.helpers import now
    if info.get('is_live') or int(info.get('duration') or 0) > 18_000:
        return f"{now()} # ignore_video {info.get('url')}"


def make_ydl_opts(config: Config) -> dict:
    media_folder = config.media_folder
    opts = {
        'format': f"{VIDEO_FORMAT}+ba[ext=m4a] / {VIDEO_FORMAT}+ba[ext=mp4] / b[ext=mp4]",
        'format_sort': [f'res:{VIDEO_RES}'],
        'outtmpl': f'{media_folder}/%(timestamp>{DATETIME_FORMAT},upload_date>{DATE_FORMAT}_u,epoch>{DATE_FORMAT}_e)s - %(title).80s [%(id).20s].%(ext)s',
        'retries': 5,
        'fragment_retries': 5,
        'extractor_retries': 5,
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'extract_flat': 'in_playlist',
        'playlist_items': '1-20',
        'match_filter': match_filter,
        'no_warnings': True,
        'quiet': True,
        'postprocessors': [{'key': 'FFmpegMetadata'}],
    }
    if config.cookies_file:
        opts['cookiefile'] = config.cookies_file
    return opts
