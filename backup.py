#!/usr/bin/env python3
"""Create a timestamped backup of bot.db using SQLite's online backup API.

The copy is written as bot.db.partial_<ts>, validated (quick_check + every
row's JSON, as for an HA snapshot) and only then renamed to
bot.db.backup_<ts>. A copy that fails is set aside as bot.db.corrupt_<ts>
(the newest one is kept) and the script exits 1; the bot's hourly backup
monitor alerts the developer about it. Neither name matches bot.db.backup_*,
so retention, the freshness monitor and the data-missing check never take a
partial or damaged file for a backup.
"""

import glob
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime

from dasovbot.database import validate_snapshot

BACKUP_PREFIX = 'bot.db.backup_'
PARTIAL_PREFIX = 'bot.db.partial_'
CORRUPT_PREFIX = 'bot.db.corrupt_'
CORRUPT_KEPT = 1


def create_backup(db_path: str, backup_path: str):
    try:
        with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(backup_path)) as dst:
            # Wait out transient locks from the live bot instead of failing;
            # busy_timeout is per-connection so both ends set their own
            src.execute("PRAGMA busy_timeout=30000")
            dst.execute("PRAGMA busy_timeout=30000")
            src.backup(dst)
    except Exception:
        # A failed backup leaves a validly-named partial file that retention
        # would otherwise treat as the newest backup
        try:
            os.remove(backup_path)
        except OSError:
            pass
        raise


def prune_backups(backup_dir: str, max_count: int, prefix: str = BACKUP_PREFIX):
    backups = []
    for path in glob.glob(os.path.join(backup_dir, f'{prefix}*')):
        name = os.path.basename(path)
        try:
            backup_time = datetime.strptime(name, f'{prefix}%Y%m%d_%H%M%S')
        except ValueError:
            continue
        backups.append((backup_time, path))

    backups.sort(reverse=True)
    for _, old_backup in backups[max_count:]:
        try:
            os.remove(old_backup)
        except OSError as e:
            print(f"Failed to remove old backup {old_backup}: {e}", file=sys.stderr)
            continue
        print(f"Removed old backup: {old_backup}")


def node_is_active(port: str, timeout: float = 5) -> bool | None:
    """Ask the bot's /health whether this node holds the HA lease.

    True/False from `role == "active"`; None when the dashboard does not
    answer (bot down or starting) — the caller treats that as "not active".
    """
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=timeout) as resp:
            return json.loads(resp.read()).get('role') == 'active'
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main():
    db_path = os.environ.get('DB_PATH', '/data/bot.db')
    backup_dir = os.environ.get('BACKUP_DIR', os.path.dirname(db_path))
    max_count = int(os.environ.get('BACKUP_MAX_COUNT', '14'))

    # /data is one Syncthing folder on both HA nodes: two cron jobs would both
    # write there and prune each other's files, and the passive copy is a
    # replica, not the source of truth. Only the ACTIVE node backs up.
    if os.environ.get('BACKUP_SKIP_ROLE_CHECK', '').lower() != 'true':
        port = os.environ.get('DASHBOARD_PORT', '8080')
        active = node_is_active(port)
        if active is None:
            print(f"Backup skipped: /health unreachable on port {port}")
            sys.exit(0)
        if not active:
            print("Backup skipped: node is passive")
            sys.exit(0)

    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'{BACKUP_PREFIX}{timestamp}')
    partial_path = os.path.join(backup_dir, f'{PARTIAL_PREFIX}{timestamp}')

    try:
        create_backup(db_path, partial_path)
    except Exception as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        validate_snapshot(partial_path)
    except (ValueError, sqlite3.DatabaseError) as e:
        corrupt_path = os.path.join(backup_dir, f'{CORRUPT_PREFIX}{timestamp}')
        os.replace(partial_path, corrupt_path)
        prune_backups(backup_dir, CORRUPT_KEPT, prefix=CORRUPT_PREFIX)
        print(f"Backup rejected, set aside as {corrupt_path}: {e}", file=sys.stderr)
        sys.exit(1)
    os.replace(partial_path, backup_path)

    size = os.path.getsize(backup_path)
    print(f"Backup created: {backup_path} ({size} bytes)")

    prune_backups(backup_dir, max_count)


if __name__ == '__main__':
    main()
