import asyncio
import logging
from dataclasses import dataclass, field

from dasovbot.config import Config
from dasovbot.models import VideoInfo, Intent, Subscription, TemporaryInlineQuery
from dasovbot.persistence import read_file, write_file
from dasovbot.helpers import now

logger = logging.getLogger(__name__)


@dataclass
class BotState:
    videos: dict[str, VideoInfo] = field(default_factory=dict)
    users: dict[str, dict] = field(default_factory=dict)
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    intents: dict[str, Intent] = field(default_factory=dict)
    temporary_inline_queries: dict[str, TemporaryInlineQuery] = field(default_factory=dict)
    download_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    config: Config = field(default=None)
    animation_file_id: str | None = None

    @classmethod
    def from_files(cls, config: Config) -> 'BotState':
        raw_videos = read_file(config.video_info_file, {})
        videos = {url: VideoInfo.from_dict(d) for url, d in raw_videos.items()}

        users = read_file(config.user_info_file, {})

        raw_subs = read_file(config.subscription_info_file, {})
        subscriptions = {url: Subscription.from_dict(d) for url, d in raw_subs.items()}

        raw_intents = read_file(config.intent_info_file, {})
        intents = {url: Intent.from_dict(d) for url, d in raw_intents.items()}

        return cls(
            videos=videos,
            users=users,
            subscriptions=subscriptions,
            intents=intents,
            temporary_inline_queries={},
            download_queue=asyncio.Queue(),
            config=config,
            animation_file_id=config.animation_file_id or None,
        )

    def save(self):
        config = self.config
        videos_dict = {url: info.to_dict() for url, info in self.videos.items()}
        write_file(config.video_info_file, videos_dict)
        write_file(config.user_info_file, self.users)
        subs_dict = {url: sub.to_dict() for url, sub in self.subscriptions.items()}
        write_file(config.subscription_info_file, subs_dict)
        intents_dict = {url: intent.to_dict() for url, intent in self.intents.items()}
        write_file(config.intent_info_file, intents_dict)
        with open(config.timestamp_file, 'w') as f:
            f.write(now())
