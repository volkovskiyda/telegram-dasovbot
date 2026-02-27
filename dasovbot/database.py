import json
import logging
import os
import time

import aiosqlite

from dasovbot.config import Config
from dasovbot.models import VideoInfo, Intent, Subscription

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intents (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    chat_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = await aiosqlite.connect(db_path)
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def migrate_from_json(db: aiosqlite.Connection, config: Config, progress: dict | None = None):
    cursor = await db.execute("SELECT COUNT(*) FROM videos")
    row = await cursor.fetchone()
    if row[0] > 0:
        if progress is not None:
            progress['status'] = 'skipped'
        return

    migrated = False
    migration_start = time.monotonic()
    batch_size = 500
    if progress is not None:
        progress['status'] = 'in_progress'
    for filepath, table, transform in [
        (config.video_info_file, 'videos', lambda k, v: (k, json.dumps(v))),
        (config.intent_info_file, 'intents', lambda k, v: (k, json.dumps(v))),
        (config.user_info_file, 'users', lambda k, v: (k, json.dumps(v))),
        (config.subscription_info_file, 'subscriptions', lambda k, v: (k, json.dumps(v))),
    ]:
        if not os.path.exists(filepath):
            continue
        try:
            with open(filepath, 'r', encoding='utf8') as f:
                data = json.load(f)
            if not data:
                continue
            total = len(data)
            column = 'chat_id' if table == 'users' else 'key'
            logger.info("Migrating %s: %d entries from %s", table, total, filepath)
            if progress is not None:
                progress['tables'][table] = {'total': total, 'done': 0}
            rows = [transform(k, v) for k, v in data.items()]
            for i in range(0, total, batch_size):
                batch = rows[i:i + batch_size]
                await db.executemany(
                    f"INSERT OR IGNORE INTO {table} ({column}, data) VALUES (?, ?)",
                    batch,
                )
                done = min(i + batch_size, total)
                if progress is not None:
                    progress['tables'][table]['done'] = done
                    progress['elapsed'] = time.monotonic() - migration_start
                if done < total or total <= batch_size:
                    logger.info("  %s: %d/%d (%.0f%%)", table, done, total, done / total * 100)
            logger.info("  %s: done (%d entries)", table, total)
            migrated = True
        except Exception:
            logger.error("Failed to migrate %s", filepath, exc_info=True)

    if migrated:
        await db.commit()
        elapsed = time.monotonic() - migration_start
        logger.info("Migration completed in %.2fs", elapsed)
        if progress is not None:
            progress['elapsed'] = elapsed
        for filepath in [
            config.video_info_file,
            config.intent_info_file,
            config.user_info_file,
            config.subscription_info_file,
        ]:
            if os.path.exists(filepath):
                from datetime import datetime
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup = f'{filepath}.migrated.{timestamp}'
                try:
                    os.rename(filepath, backup)
                    logger.info("Renamed %s -> %s", filepath, backup)
                except Exception:
                    logger.error("Failed to rename %s", filepath, exc_info=True)

    if progress is not None and progress['status'] != 'completed':
        progress['status'] = 'completed' if migrated else 'skipped'


# --- Videos ---

async def upsert_video(db: aiosqlite.Connection, key: str, video: VideoInfo):
    await db.execute(
        "INSERT OR REPLACE INTO videos (key, data) VALUES (?, ?)",
        (key, json.dumps(video.to_dict())),
    )
    await db.commit()


async def delete_video(db: aiosqlite.Connection, key: str):
    await db.execute("DELETE FROM videos WHERE key = ?", (key,))
    await db.commit()


async def load_videos(db: aiosqlite.Connection) -> dict[str, VideoInfo]:
    cursor = await db.execute("SELECT key, data FROM videos")
    rows = await cursor.fetchall()
    return {key: VideoInfo.from_dict(json.loads(data)) for key, data in rows}


# --- Intents ---

async def upsert_intent(db: aiosqlite.Connection, key: str, intent: Intent):
    await db.execute(
        "INSERT OR REPLACE INTO intents (key, data) VALUES (?, ?)",
        (key, json.dumps(intent.to_dict())),
    )
    await db.commit()


async def delete_intent(db: aiosqlite.Connection, key: str):
    await db.execute("DELETE FROM intents WHERE key = ?", (key,))
    await db.commit()


async def load_intents(db: aiosqlite.Connection) -> dict[str, Intent]:
    cursor = await db.execute("SELECT key, data FROM intents")
    rows = await cursor.fetchall()
    return {key: Intent.from_dict(json.loads(data)) for key, data in rows}


# --- Users ---

async def upsert_user(db: aiosqlite.Connection, chat_id: str, data: dict):
    await db.execute(
        "INSERT OR REPLACE INTO users (chat_id, data) VALUES (?, ?)",
        (chat_id, json.dumps(data)),
    )
    await db.commit()


async def load_users(db: aiosqlite.Connection) -> dict[str, dict]:
    cursor = await db.execute("SELECT chat_id, data FROM users")
    rows = await cursor.fetchall()
    return {chat_id: json.loads(data) for chat_id, data in rows}


# --- Subscriptions ---

async def upsert_subscription(db: aiosqlite.Connection, key: str, sub: Subscription):
    await db.execute(
        "INSERT OR REPLACE INTO subscriptions (key, data) VALUES (?, ?)",
        (key, json.dumps(sub.to_dict())),
    )
    await db.commit()


async def delete_subscription(db: aiosqlite.Connection, key: str):
    await db.execute("DELETE FROM subscriptions WHERE key = ?", (key,))
    await db.commit()


async def load_subscriptions(db: aiosqlite.Connection) -> dict[str, Subscription]:
    cursor = await db.execute("SELECT key, data FROM subscriptions")
    rows = await cursor.fetchall()
    return {key: Subscription.from_dict(json.loads(data)) for key, data in rows}
