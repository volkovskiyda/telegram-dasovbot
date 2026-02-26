import asyncio
import logging
from dataclasses import dataclass, field

import aiosqlite

from dasovbot.config import Config
from dasovbot.models import VideoInfo, Intent, Subscription, TemporaryInlineQuery

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
    background_task_status: dict[str, str] = field(default_factory=dict)
    db: aiosqlite.Connection = field(default=None)

    @classmethod
    async def from_database(cls, config: Config) -> 'BotState':
        from dasovbot.database import init_db, migrate_from_json, load_videos, load_intents, load_users, load_subscriptions

        db = await init_db(config.db_file)
        await migrate_from_json(db, config)

        videos = await load_videos(db)
        users = await load_users(db)
        subscriptions = await load_subscriptions(db)
        intents = await load_intents(db)

        return cls(
            videos=videos,
            users=users,
            subscriptions=subscriptions,
            intents=intents,
            temporary_inline_queries={},
            download_queue=asyncio.Queue(),
            config=config,
            animation_file_id=config.animation_file_id or None,
            db=db,
        )

    async def set_video(self, key: str, video: VideoInfo):
        from dasovbot.database import upsert_video
        self.videos[key] = video
        await upsert_video(self.db, key, video)

    async def set_intent(self, key: str, intent: Intent):
        from dasovbot.database import upsert_intent
        self.intents[key] = intent
        await upsert_intent(self.db, key, intent)

    async def save_intent(self, key: str):
        from dasovbot.database import upsert_intent
        intent = self.intents.get(key)
        if intent:
            await upsert_intent(self.db, key, intent)

    async def pop_intent(self, key: str) -> Intent | None:
        from dasovbot.database import delete_intent
        intent = self.intents.pop(key, None)
        await delete_intent(self.db, key)
        return intent

    async def set_user(self, chat_id: str, data: dict):
        from dasovbot.database import upsert_user
        self.users[chat_id] = data
        await upsert_user(self.db, chat_id, data)

    async def set_subscription(self, key: str, sub: Subscription):
        from dasovbot.database import upsert_subscription
        self.subscriptions[key] = sub
        await upsert_subscription(self.db, key, sub)

    async def pop_subscription(self, key: str) -> Subscription | None:
        from dasovbot.database import delete_subscription
        sub = self.subscriptions.pop(key, None)
        await delete_subscription(self.db, key)
        return sub

    async def add_subscriber(self, key: str, chat_id: str):
        from dasovbot.database import upsert_subscription
        sub = self.subscriptions.get(key)
        if sub and chat_id not in sub.chat_ids:
            sub.chat_ids.append(chat_id)
            await upsert_subscription(self.db, key, sub)

    async def remove_subscriber(self, key: str, chat_id: str):
        from dasovbot.database import upsert_subscription, delete_subscription
        sub = self.subscriptions.get(key)
        if not sub:
            return
        sub.chat_ids[:] = (item for item in sub.chat_ids if item != chat_id)
        if not sub.chat_ids:
            self.subscriptions.pop(key, None)
            await delete_subscription(self.db, key)
        else:
            await upsert_subscription(self.db, key, sub)

    async def close(self):
        if self.db:
            await self.db.close()
