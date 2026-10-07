import glob
import json
import logging
import os
import sqlite3
import time
from contextlib import closing

import aiosqlite

from dasovbot.config import Config
from dasovbot.models import VideoInfo, Intent, Subscription

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS intents (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    chat_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS subscriptions (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS banned_users (
    user_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT,
    requested_at TEXT NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tombstones (
    tbl TEXT NOT NULL,
    key TEXT NOT NULL,
    rev INTEGER NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY (tbl, key)
);
"""

# Tables that carry a per-row rev and take part in the HA change feed, with
# the name of their primary-key column (``requests`` is append-only and keyed
# by its autoincrement id instead)
KEYED_TABLES = {
    'videos': 'key',
    'intents': 'key',
    'users': 'chat_id',
    'subscriptions': 'key',
    'banned_users': 'user_id',
}
REV_TABLES = [*KEYED_TABLES, 'requests']

REV_KEY = 'rev'  # sync_meta key holding this node's revision counter


async def init_db(db_path: str) -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = await aiosqlite.connect(db_path)
    # WAL: backup.py runs Connection.backup from a separate process, and in
    # rollback-journal mode its whole-file read lock makes bot commits fail
    # with "database is locked" once the copy outlasts the 5s default wait.
    # WAL readers never block writers, so the backup window stops mattering.
    # Persistent (stored in the DB file); the -wal/-shm companion files next
    # to bot.db are expected. busy_timeout is per-connection: wait out any
    # residual lock (e.g. a checkpoint) instead of raising immediately.
    # Timeout first: the one-time WAL conversion needs a moment of exclusive
    # access, and must wait out e.g. a running backup rather than abort startup
    await db.execute("PRAGMA busy_timeout=30000")
    await db.execute("PRAGMA journal_mode=WAL")
    await db.executescript(SCHEMA)
    await migrate_schema_ha(db)
    await db.commit()
    return db


async def migrate_schema_ha(db: aiosqlite.Connection):
    """Bring a pre-HA database up to the rev/tombstone schema. Idempotent.

    Adds the ``rev`` column where it is missing, backfills every rev-0 row with
    a globally unique revision (rowid + a per-table offset, in table order, so
    the change-feed cursor can page across tables), seeds the node counter in
    ``sync_meta``, and purges exact duplicates from ``requests`` before its
    dedupe index is created. Runs inside the caller's transaction; does not
    commit.
    """
    started = time.monotonic()
    for table in REV_TABLES:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}
        if 'rev' not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
            logger.info("Schema: added rev column to %s", table)
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_rev ON {table}(rev)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tombstones_rev ON tombstones(rev)")

    # Backfill: revs must be unique across tables, so each table starts where
    # the previous one ended. Rows written by the rev-aware code already carry
    # a rev > 0 and are left alone.
    offset = await load_rev(db)
    backfilled = 0
    for table in REV_TABLES:
        cursor = await db.execute(f"UPDATE {table} SET rev = rowid + ? WHERE rev = 0", (offset,))
        backfilled += cursor.rowcount
        cursor = await db.execute(f"SELECT COALESCE(MAX(rev), 0) FROM {table}")
        offset = max(offset, (await cursor.fetchone())[0])
    await persist_rev(db, offset)

    cursor = await db.execute(
        "DELETE FROM requests WHERE id NOT IN "
        "(SELECT MIN(id) FROM requests GROUP BY user_id, url, requested_at)"
    )
    purged = cursor.rowcount
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_requests_dedupe ON requests(user_id, url, requested_at)"
    )
    if backfilled or purged:
        logger.info(
            "Schema: HA migration backfilled %d revs (counter %d), purged %d duplicate requests in %.2fs",
            backfilled, offset, purged, time.monotonic() - started,
        )


# --- sync_meta ---

async def get_meta(db: aiosqlite.Connection, key: str, default=None):
    cursor = await db.execute("SELECT value FROM sync_meta WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return json.loads(row[0]) if row else default


async def set_meta(db: aiosqlite.Connection, key: str, value):
    """Upsert a JSON-encoded sync_meta value. Does not commit."""
    await db.execute(
        "INSERT INTO sync_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


async def load_rev(db: aiosqlite.Connection) -> int:
    return int(await get_meta(db, REV_KEY, 0))


async def persist_rev(db: aiosqlite.Connection, rev: int):
    """Raise the stored counter to ``rev``; never lowers it. Does not commit.

    Writers interleave on one connection, so a slower coroutine persisting an
    older rev must not undo a newer one that is already in a row.
    """
    await db.execute(
        "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = MAX(CAST(value AS INTEGER), CAST(excluded.value AS INTEGER))",
        (REV_KEY, str(int(rev))),
    )


async def _upsert(db: aiosqlite.Connection, table: str, key: str, data: str, rev: int):
    column = KEYED_TABLES[table]
    await db.execute(
        f"INSERT OR REPLACE INTO {table} ({column}, data, rev) VALUES (?, ?, ?)",
        (key, data, rev),
    )
    # A key written again after a delete is alive: its tombstone must not
    # keep deleting it on the peer
    await db.execute("DELETE FROM tombstones WHERE tbl = ? AND key = ?", (table, key))
    await persist_rev(db, rev)
    await db.commit()


async def _delete(db: aiosqlite.Connection, table: str, key: str, rev: int, deleted_at: str):
    column = KEYED_TABLES[table]
    cursor = await db.execute(f"DELETE FROM {table} WHERE {column} = ?", (key,))
    if cursor.rowcount > 0:
        # Only a key that existed gets a tombstone; deleting a missing key
        # (e.g. pop_intent on an unknown query) must not invent one
        await db.execute(
            "INSERT OR REPLACE INTO tombstones (tbl, key, rev, deleted_at) VALUES (?, ?, ?, ?)",
            (table, key, rev, deleted_at),
        )
    await persist_rev(db, rev)
    await db.commit()


async def migrate_from_json(db: aiosqlite.Connection, config: Config, progress: dict | None = None):
    cursor = await db.execute("SELECT COUNT(*) FROM videos")
    row = await cursor.fetchone()
    if row[0] > 0:
        logger.info("Migration: skipped (database already populated)")
        if progress is not None:
            progress['status'] = 'skipped'
        return

    logger.info("Migration: started")
    migrated = False
    migrated_files = []
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
            migrated_files.append(filepath)
        except Exception:
            logger.error("Migration: error migrating %s", filepath, exc_info=True)

    if migrated:
        await db.commit()
        elapsed = time.monotonic() - migration_start
        logger.info("Migration: finished in %.2fs", elapsed)
        if progress is not None:
            progress['elapsed'] = elapsed
        # Rename only files that migrated successfully; a file whose migration
        # raised must stay in place so a later run can retry it.
        for filepath in migrated_files:
            if os.path.exists(filepath):
                from datetime import datetime
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup = f'{filepath}.migrated.{timestamp}'
                try:
                    os.rename(filepath, backup)
                    logger.info("Renamed %s -> %s", filepath, backup)
                except Exception:
                    logger.error("Failed to rename %s", filepath, exc_info=True)

    if not migrated:
        logger.info("Migration: skipped (no JSON files to migrate)")
    if progress is not None and progress['status'] != 'completed':
        progress['status'] = 'completed' if migrated else 'skipped'


async def warn_if_data_missing(db: aiosqlite.Connection, db_path: str) -> str | None:
    """Guard against a misrouted DB path silently starting fresh.

    If every table is empty but a populated ``bot.db.backup_*`` sits next to
    the live database, the bot is almost certainly pointed at the wrong path
    (a stray CONFIG_FOLDER, an unmounted volume) and is about to accumulate
    new data over an empty file while the real data waits in the backup.

    Returns a human-readable warning message when this condition is detected
    (also logged), or None when the data looks healthy.
    """
    cursor = await db.execute("SELECT COUNT(*) FROM videos")
    if (await cursor.fetchone())[0] > 0:
        return None

    backup_dir = os.path.dirname(db_path) or '.'
    # Newest first: the timestamped names sort lexicographically by time
    backups = sorted(glob.glob(os.path.join(backup_dir, 'bot.db.backup_*')), reverse=True)
    for backup in backups:
        try:
            with closing(sqlite3.connect(backup)) as conn:
                if conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] > 0:
                    message = (
                        f"Live database {db_path} is empty but backup {backup} holds data. The bot may "
                        f"be pointed at the wrong path (check CONFIG_FOLDER and volume mounts) before it "
                        f"overwrites the empty file. To restore: stop the bot and run  cp {backup} {db_path}"
                    )
                    logger.warning(message)
                    return message
        except sqlite3.Error:
            continue
    return None


# --- Videos ---

async def upsert_video(db: aiosqlite.Connection, key: str, video: VideoInfo, rev: int):
    await _upsert(db, 'videos', key, json.dumps(video.to_dict()), rev)


async def delete_video(db: aiosqlite.Connection, key: str, rev: int, deleted_at: str):
    await _delete(db, 'videos', key, rev, deleted_at)


async def load_videos(db: aiosqlite.Connection) -> dict[str, VideoInfo]:
    cursor = await db.execute("SELECT key, data FROM videos")
    rows = await cursor.fetchall()
    return {key: VideoInfo.from_dict(json.loads(data)) for key, data in rows}


# --- Intents ---

async def upsert_intent(db: aiosqlite.Connection, key: str, intent: Intent, rev: int):
    await _upsert(db, 'intents', key, json.dumps(intent.to_dict()), rev)


async def delete_intent(db: aiosqlite.Connection, key: str, rev: int, deleted_at: str):
    await _delete(db, 'intents', key, rev, deleted_at)


async def load_intents(db: aiosqlite.Connection) -> dict[str, Intent]:
    cursor = await db.execute("SELECT key, data FROM intents")
    rows = await cursor.fetchall()
    return {key: Intent.from_dict(json.loads(data)) for key, data in rows}


# --- Users ---

async def upsert_user(db: aiosqlite.Connection, chat_id: str, data: dict, rev: int):
    await _upsert(db, 'users', chat_id, json.dumps(data), rev)


async def load_users(db: aiosqlite.Connection) -> dict[str, dict]:
    cursor = await db.execute("SELECT chat_id, data FROM users")
    rows = await cursor.fetchall()
    return {chat_id: json.loads(data) for chat_id, data in rows}


# --- Subscriptions ---

async def upsert_subscription(db: aiosqlite.Connection, key: str, sub: Subscription, rev: int):
    await _upsert(db, 'subscriptions', key, json.dumps(sub.to_dict()), rev)


async def delete_subscription(db: aiosqlite.Connection, key: str, rev: int, deleted_at: str):
    await _delete(db, 'subscriptions', key, rev, deleted_at)


async def load_subscriptions(db: aiosqlite.Connection) -> dict[str, Subscription]:
    cursor = await db.execute("SELECT key, data FROM subscriptions")
    rows = await cursor.fetchall()
    return {key: Subscription.from_dict(json.loads(data)) for key, data in rows}


# --- Banned users ---

async def upsert_banned_user(db: aiosqlite.Connection, user_id: str, data: dict, rev: int):
    await _upsert(db, 'banned_users', user_id, json.dumps(data), rev)


async def delete_banned_user(db: aiosqlite.Connection, user_id: str, rev: int, deleted_at: str):
    await _delete(db, 'banned_users', user_id, rev, deleted_at)


async def load_banned_users(db: aiosqlite.Connection) -> dict[str, dict]:
    cursor = await db.execute("SELECT user_id, data FROM banned_users")
    rows = await cursor.fetchall()
    return {user_id: json.loads(data) for user_id, data in rows}


# --- Requests ---

async def insert_request(db: aiosqlite.Connection, user_id: str, url: str, source: str | None,
                         requested_at: str, rev: int):
    # OR IGNORE: (user_id, url, requested_at) is unique so the same row
    # arriving twice through the HA feed is a no-op
    await db.execute(
        "INSERT OR IGNORE INTO requests (user_id, url, source, requested_at, rev) VALUES (?, ?, ?, ?, ?)",
        (user_id, url, source, requested_at, rev),
    )
    await persist_rev(db, rev)
    await db.commit()


async def load_request_stats(db: aiosqlite.Connection) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """Aggregate the request log into (url -> requester ids, user id -> stats).

    Requester ids keep first-request order; stats hold the request count and
    the latest request timestamp.
    """
    cursor = await db.execute(
        "SELECT url, user_id FROM requests GROUP BY url, user_id ORDER BY MIN(id)"
    )
    video_requesters: dict[str, list[str]] = {}
    for url, user_id in await cursor.fetchall():
        video_requesters.setdefault(url, []).append(user_id)
    cursor = await db.execute(
        "SELECT user_id, COUNT(*), MAX(requested_at) FROM requests GROUP BY user_id"
    )
    user_requests = {
        user_id: {'count': count, 'last_at': last_at}
        for user_id, count, last_at in await cursor.fetchall()
    }
    return video_requesters, user_requests


# --- HA change feed ---
#
# Every rev-carrying table takes part in one feed ordered by rev. The passive
# node pulls pages of "everything with rev in (since, until]" and applies them
# with the remote revs unchanged (Lamport clock: the local counter is only
# raised to the page's `until`). Tombstones travel in the same pages.

FEED_TABLES = [*REV_TABLES, 'tombstones']


def last_applied_key(peer: str) -> str:
    return f'last_applied_rev:{peer}'


async def read_changes(db: aiosqlite.Connection, since: int, limit: int) -> dict:
    """One page of the change feed: rows and tombstones with since < rev <= until.

    `until` is chosen so a page never splits rows that share one rev (rows
    applied from the peer may collide in number): it is the rev of the
    `limit`-th candidate row, or the current counter when fewer remain. A
    `since` at or beyond the counter yields an empty page with until == since.
    """
    since = int(since)
    union = " UNION ALL ".join(f"SELECT rev FROM {t} WHERE rev > ?" for t in FEED_TABLES)
    cursor = await db.execute(
        f"SELECT rev FROM ({union}) ORDER BY rev LIMIT 1 OFFSET ?",
        (*([since] * len(FEED_TABLES)), max(int(limit), 1) - 1),
    )
    row = await cursor.fetchone()
    if row:
        until, has_more = int(row[0]), True
    else:
        # Fewer than `limit` rows remain: the page ends at the highest rev in
        # play. The counter normally is that maximum; the table scan guards
        # against rows stamped past a counter that was not persisted yet
        union_max = " UNION ALL ".join(f"SELECT MAX(rev) AS rev FROM {t}" for t in FEED_TABLES)
        cursor = await db.execute(f"SELECT MAX(rev) FROM ({union_max})")
        top = (await cursor.fetchone())[0] or 0
        until, has_more = max(await load_rev(db), int(top), since), False

    rows = {}
    for table, column in KEYED_TABLES.items():
        cursor = await db.execute(
            f"SELECT {column}, data, rev FROM {table} WHERE rev > ? AND rev <= ? ORDER BY rev",
            (since, until),
        )
        rows[table] = [list(r) for r in await cursor.fetchall()]
    cursor = await db.execute(
        "SELECT user_id, url, source, requested_at, rev FROM requests WHERE rev > ? AND rev <= ? ORDER BY rev",
        (since, until),
    )
    rows['requests'] = [list(r) for r in await cursor.fetchall()]
    cursor = await db.execute(
        "SELECT tbl, key, rev, deleted_at FROM tombstones WHERE rev > ? AND rev <= ? ORDER BY rev",
        (since, until),
    )
    tombstones = [list(r) for r in await cursor.fetchall()]
    return {'since': since, 'until': until, 'has_more': has_more, 'rows': rows, 'tombstones': tombstones}


async def apply_changes(db: aiosqlite.Connection, page: dict, peer: str) -> dict:
    """Apply one feed page in a single transaction; the caller updates memory.

    Rows overwrite the local copy with the remote rev and clear any tombstone
    for their key. A tombstone deletes the local row only when the row is
    absent or not newer than the tombstone ("tombstones win over older
    rows"). `requests` rows are appended, duplicates ignored. Finally the
    counter is raised to the page's `until` and the per-peer cursor stored.

    Returns what changed so BotState can mirror it:
    {'rows': {table: [[key, data, rev], ...]}, 'deleted': [[tbl, key], ...],
     'requests': [[user_id, url, source, requested_at], ...] (newly inserted),
     'skipped': n, 'until': until}
    """
    applied = {table: [] for table in KEYED_TABLES}
    deleted, new_requests, skipped = [], [], 0

    for table, column in KEYED_TABLES.items():
        for key, data, rev in page['rows'].get(table, []):
            await db.execute(
                f"INSERT OR REPLACE INTO {table} ({column}, data, rev) VALUES (?, ?, ?)",
                (key, data, rev),
            )
            await db.execute("DELETE FROM tombstones WHERE tbl = ? AND key = ?", (table, key))
            applied[table].append([key, data, rev])

    for user_id, url, source, requested_at, rev in page['rows'].get('requests', []):
        cursor = await db.execute(
            "INSERT OR IGNORE INTO requests (user_id, url, source, requested_at, rev) VALUES (?, ?, ?, ?, ?)",
            (user_id, url, source, requested_at, rev),
        )
        if cursor.rowcount == 1:
            new_requests.append([user_id, url, source, requested_at])

    for tbl, key, rev, deleted_at in page.get('tombstones', []):
        column = KEYED_TABLES.get(tbl)
        if column is None:
            skipped += 1
            continue
        cursor = await db.execute(f"SELECT rev FROM {tbl} WHERE {column} = ?", (key,))
        row = await cursor.fetchone()
        if row is not None and row[0] > rev:
            skipped += 1  # local row is newer than the tombstone
            continue
        await db.execute(f"DELETE FROM {tbl} WHERE {column} = ?", (key,))
        await db.execute(
            "INSERT OR REPLACE INTO tombstones (tbl, key, rev, deleted_at) VALUES (?, ?, ?, ?)",
            (tbl, key, rev, deleted_at),
        )
        deleted.append([tbl, key])

    until = int(page['until'])
    await persist_rev(db, until)
    await set_meta(db, last_applied_key(peer), until)
    await db.commit()
    return {'rows': applied, 'deleted': deleted, 'requests': new_requests, 'skipped': skipped, 'until': until}


async def write_snapshot(db: aiosqlite.Connection, path: str):
    """Write a consistent copy of the live database to `path` (SQLite backup API)."""
    # The backup runs on aiosqlite's worker thread, so the target must not
    # be bound to the thread that opened it
    target = sqlite3.connect(path, check_same_thread=False)
    try:
        await db.backup(target)
    finally:
        target.close()


async def reconcile_from_snapshot(db: aiosqlite.Connection, snapshot_path: str, peer: str) -> dict:
    """Make the keyed tables and tombstones equal to the peer's snapshot; union requests.

    Self-healing floor for the passive node: anything the incremental feed
    missed is corrected here. Rows identical in key, rev and data are left
    untouched. Only the snapshot's counter is read from its sync_meta — every
    other key there is the peer's own metadata.
    """
    await db.execute("ATTACH DATABASE ? AS snap", (snapshot_path,))
    try:
        changed = {}
        for table, column in KEYED_TABLES.items():
            cursor = await db.execute(
                f"INSERT OR REPLACE INTO main.{table} ({column}, data, rev) "
                f"SELECT s.{column}, s.data, s.rev FROM snap.{table} s WHERE NOT EXISTS "
                f"(SELECT 1 FROM main.{table} l WHERE l.{column} = s.{column} AND l.rev = s.rev AND l.data = s.data)"
            )
            upserted = cursor.rowcount
            cursor = await db.execute(
                f"DELETE FROM main.{table} WHERE {column} NOT IN (SELECT {column} FROM snap.{table})"
            )
            changed[table] = {'upserted': upserted, 'deleted': cursor.rowcount}
        cursor = await db.execute(
            "INSERT OR REPLACE INTO main.tombstones (tbl, key, rev, deleted_at) "
            "SELECT s.tbl, s.key, s.rev, s.deleted_at FROM snap.tombstones s WHERE NOT EXISTS "
            "(SELECT 1 FROM main.tombstones l WHERE l.tbl = s.tbl AND l.key = s.key AND l.rev = s.rev)"
        )
        upserted = cursor.rowcount
        cursor = await db.execute(
            "DELETE FROM main.tombstones WHERE (tbl, key) NOT IN (SELECT tbl, key FROM snap.tombstones)"
        )
        changed['tombstones'] = {'upserted': upserted, 'deleted': cursor.rowcount}
        cursor = await db.execute(
            "INSERT OR IGNORE INTO main.requests (user_id, url, source, requested_at, rev) "
            "SELECT user_id, url, source, requested_at, rev FROM snap.requests"
        )
        changed['requests'] = {'upserted': cursor.rowcount, 'deleted': 0}

        cursor = await db.execute("SELECT value FROM snap.sync_meta WHERE key = ?", (REV_KEY,))
        row = await cursor.fetchone()
        snap_rev = int(row[0]) if row else 0
        await persist_rev(db, snap_rev)
        await set_meta(db, last_applied_key(peer), snap_rev)
        await db.commit()
    finally:
        # DETACH is refused inside a transaction, so it follows the commit
        # (or a rollback on the error path)
        try:
            await db.execute("DETACH DATABASE snap")
        except sqlite3.OperationalError:
            await db.rollback()
            await db.execute("DETACH DATABASE snap")
    return {'changed': changed, 'snapshot_rev': snap_rev}


async def keys_above_rev(db: aiosqlite.Connection, rev: int) -> dict:
    """Keys (per keyed table) and tombstone (tbl, key) pairs with rev > `rev`."""
    result = {}
    for table, column in KEYED_TABLES.items():
        cursor = await db.execute(f"SELECT {column} FROM {table} WHERE rev > ?", (rev,))
        result[table] = [r[0] for r in await cursor.fetchall()]
    cursor = await db.execute("SELECT tbl, key FROM tombstones WHERE rev > ?", (rev,))
    result['tombstones'] = [(r[0], r[1]) for r in await cursor.fetchall()]
    return result


async def restamp(db: aiosqlite.Connection, table: str, key: str, rev: int) -> bool:
    """Give a surviving local row a fresh rev so it flows to the peer. No commit."""
    column = KEYED_TABLES[table]
    cursor = await db.execute(f"UPDATE {table} SET rev = ? WHERE {column} = ?", (rev, key))
    await persist_rev(db, rev)
    return cursor.rowcount == 1


async def restamp_tombstone(db: aiosqlite.Connection, table: str, key: str, rev: int) -> bool:
    cursor = await db.execute("UPDATE tombstones SET rev = ? WHERE tbl = ? AND key = ?", (rev, table, key))
    await persist_rev(db, rev)
    return cursor.rowcount == 1
