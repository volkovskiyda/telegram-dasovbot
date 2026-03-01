import os
from dataclasses import dataclass

import dotenv

from dasovbot.constants import DATETIME_FORMAT, DATE_FORMAT, VIDEO_FORMAT


@dataclass
class Config:
    bot_token: str
    base_url: str
    developer_chat_id: str
    developer_id: str
    read_timeout: float = 30.0
    loading_video_id: str = ""
    animation_file_id: str = ""
    config_folder: str = "/"
    empty_media_folder: bool = False
    cookies_file: str = ""

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
    def timestamp_file(self) -> str:
        return f'{self.config_folder}/data/timestamp.txt'

    @property
    def db_file(self) -> str:
        return f'{self.config_folder}/data/bot.db'

    @property
    def media_folder(self) -> str:
        return f'{self.config_folder}/media'


def load_config() -> Config:
    dotenv.load_dotenv()

    required_env = ['BOT_TOKEN', 'BASE_URL', 'DEVELOPER_CHAT_ID']
    missing = [var for var in required_env if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    developer_chat_id = os.getenv('DEVELOPER_CHAT_ID')
    config_folder = os.getenv('CONFIG_FOLDER') or '/'

    return Config(
        bot_token=os.getenv('BOT_TOKEN'),
        base_url=os.getenv('BASE_URL'),
        developer_chat_id=developer_chat_id,
        developer_id=os.getenv('DEVELOPER_ID') or developer_chat_id,
        read_timeout=float(os.getenv('READ_TIMEOUT') or 30),
        loading_video_id=os.getenv('LOADING_VIDEO_ID') or '',
        animation_file_id=os.getenv('ANIMATION_FILE_ID') or '',
        config_folder=config_folder,
        empty_media_folder=os.getenv('EMPTY_MEDIA_FOLDER', 'false').lower() == 'true',
        cookies_file=os.getenv('COOKIES_FILE') or '',
    )


def match_filter(info, *, incomplete):
    from dasovbot.helpers import now
    if info.get('is_live') or int(info.get('duration') or 0) > 15_000:
        return f"{now()} # ignore_video {info.get('url')}"


def make_ydl_opts(config: Config) -> dict:
    media_folder = config.media_folder
    opts = {
        'format': f"{VIDEO_FORMAT}+ba[ext=m4a] / {VIDEO_FORMAT}+ba[ext=mp4] / b[ext=mp4][height<=?720]",
        'outtmpl': f'{media_folder}/%(timestamp>{DATETIME_FORMAT},upload_date>{DATE_FORMAT}_u,epoch>{DATE_FORMAT}_e)s - %(title).80s [%(id).20s].%(ext)s',
        'retries': 5,
        'fragment_retries': 5,
        'extractor_retries': 5,
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
