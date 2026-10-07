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
    # Aggregates of the persisted request log: url -> requester user ids, and
    # user id -> {'count', 'last_at'}
    video_requesters: dict[str, list[str]] = field(default_factory=dict)
    user_requests: dict[str, dict] = field(default_factory=dict)
    # user id -> {'banned_at', 'name'}
    banned_users: dict[str, dict] = field(default_factory=dict)
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    intents: dict[str, Intent] = field(default_factory=dict)
    temporary_inline_queries: dict[str, TemporaryInlineQuery] = field(default_factory=dict)
    download_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    # Caps concurrent file uploads (UPLOAD_CONCURRENCY, default 1): the intent
    # worker and background tasks each send multi-GB videos, and overlapping
    # sends multiply peak memory/IO
    upload_semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    config: Config = field(default=None)
    animation_file_id: str | None = None
    background_task_status: dict[str, str] = field(default_factory=dict)
    # Strong references: the event loop only keeps weak refs to tasks
    background_tasks: set = field(default_factory=set)
    migration_progress: dict = field(default_factory=dict)
    # Health warnings surfaced on the dashboard, keyed by a stable id so an
    # ongoing condition updates in place instead of piling up duplicates
    health_alerts: dict[str, dict] = field(default_factory=dict)
    # Monotonic deadlines before which a failed intent must not be retried.
    # Deliberately not persisted: after a process restart no abandoned
    # download thread can exist, so an immediate retry is safe.
    intent_retry_after: dict[str, float] = field(default_factory=dict)
    # This node's revision counter (Lamport-style): every local write stamps
    # ++rev on its row; rows applied from the HA peer keep the peer's rev and
    # only raise the counter. Loaded from sync_meta in migrate_and_load()
    rev: int = 0
    db: aiosqlite.Connection = field(default=None)

    @classmethod
    async def create(cls, config: Config) -> 'BotState':
        from dasovbot.database import init_db

        db = await init_db(config.db_file)
        return cls(
            config=config,
            animation_file_id=config.animation_file_id or None,
            upload_semaphore=asyncio.Semaphore(config.upload_concurrency),
            db=db,
            migration_progress={'status': 'pending', 'tables': {}, 'elapsed': 0.0},
        )

    def set_alert(self, alert_id: str, message: str, level: str = 'warning'):
        from dasovbot.helpers import now
        existing = self.health_alerts.get(alert_id)
        self.health_alerts[alert_id] = {
            'level': level,
            'message': message,
            # Preserve the original onset time while the condition persists
            'since': existing['since'] if existing else now(),
        }

    def clear_alert(self, alert_id: str):
        self.health_alerts.pop(alert_id, None)

    async def migrate_and_load(self):
        from dasovbot.database import (
            migrate_from_json, warn_if_data_missing,
            load_videos, load_intents, load_users, load_subscriptions,
            load_request_stats, load_banned_users, load_rev,
        )

        await migrate_from_json(self.db, self.config, self.migration_progress)
        warning = await warn_if_data_missing(self.db, self.config.db_file)
        if warning:
            self.set_alert('data_missing', warning, level='error')

        self.rev = await load_rev(self.db)
        self.videos = await load_videos(self.db)
        self.users = await load_users(self.db)
        self.subscriptions = await load_subscriptions(self.db)
        self.intents = await load_intents(self.db)
        self.video_requesters, self.user_requests = await load_request_stats(self.db)
        self.banned_users = await load_banned_users(self.db)

    def next_rev(self) -> int:
        self.rev += 1
        return self.rev

    async def set_video(self, key: str, video: VideoInfo):
        from dasovbot.database import upsert_video
        self.videos[key] = video
        await upsert_video(self.db, key, video, self.next_rev())

    async def set_intent(self, key: str, intent: Intent):
        from dasovbot.database import upsert_intent
        self.intents[key] = intent
        await upsert_intent(self.db, key, intent, self.next_rev())

    async def save_intent(self, key: str):
        from dasovbot.database import upsert_intent
        intent = self.intents.get(key)
        if intent:
            await upsert_intent(self.db, key, intent, self.next_rev())

    async def pop_intent(self, key: str) -> Intent | None:
        from dasovbot.database import delete_intent
        from dasovbot.helpers import now
        intent = self.intents.pop(key, None)
        self.intent_retry_after.pop(key, None)
        await delete_intent(self.db, key, self.next_rev(), now())
        return intent

    async def set_user(self, chat_id: str, data: dict):
        from dasovbot.database import upsert_user
        self.users[chat_id] = data
        await upsert_user(self.db, chat_id, data, self.next_rev())

    async def record_request(self, user_id, url: str, source: str | None):
        from dasovbot.database import insert_request
        from dasovbot.helpers import now
        user_id = str(user_id)
        requested_at = now()
        requesters = self.video_requesters.setdefault(url, [])
        if user_id not in requesters:
            requesters.append(user_id)
        stats = self.user_requests.setdefault(user_id, {'count': 0, 'last_at': None})
        stats['count'] += 1
        stats['last_at'] = requested_at
        await insert_request(self.db, user_id, url, source, requested_at, self.next_rev())

    def is_banned(self, user_id) -> bool:
        return str(user_id) in self.banned_users

    async def ban_user(self, user_id, name: str = ''):
        from dasovbot.database import upsert_banned_user
        from dasovbot.helpers import now
        user_id = str(user_id)
        data = {'banned_at': now(), 'name': name}
        self.banned_users[user_id] = data
        await upsert_banned_user(self.db, user_id, data, self.next_rev())

    async def unban_user(self, user_id):
        from dasovbot.database import delete_banned_user
        from dasovbot.helpers import now
        user_id = str(user_id)
        self.banned_users.pop(user_id, None)
        await delete_banned_user(self.db, user_id, self.next_rev(), now())

    async def set_subscription(self, key: str, sub: Subscription):
        from dasovbot.database import upsert_subscription
        self.subscriptions[key] = sub
        await upsert_subscription(self.db, key, sub, self.next_rev())

    async def pop_subscription(self, key: str) -> Subscription | None:
        from dasovbot.database import delete_subscription
        from dasovbot.helpers import now
        sub = self.subscriptions.pop(key, None)
        await delete_subscription(self.db, key, self.next_rev(), now())
        return sub

    async def add_subscriber(self, key: str, chat_id: str):
        from dasovbot.database import upsert_subscription
        sub = self.subscriptions.get(key)
        if sub and chat_id not in sub.chat_ids:
            sub.chat_ids.append(chat_id)
            await upsert_subscription(self.db, key, sub, self.next_rev())

    async def remove_subscriber(self, key: str, chat_id: str):
        from dasovbot.database import upsert_subscription, delete_subscription
        from dasovbot.helpers import now
        sub = self.subscriptions.get(key)
        if not sub:
            return
        sub.chat_ids[:] = (item for item in sub.chat_ids if item != chat_id)
        if not sub.chat_ids:
            self.subscriptions.pop(key, None)
            await delete_subscription(self.db, key, self.next_rev(), now())
        else:
            await upsert_subscription(self.db, key, sub, self.next_rev())

    async def close(self):
        if self.db:
            await self.db.close()
