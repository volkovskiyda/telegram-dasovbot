#!/usr/bin/env python3
"""Create a timestamped backup of bot.db using SQLite's online backup API."""

import glob
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime


def create_backup(db_path: str, backup_path: str):
    try:
        with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(backup_path)) as dst:
            src.backup(dst)
    except Exception:
        # A failed backup leaves a validly-named partial file that retention
        # would otherwise treat as the newest backup
        try:
            os.remove(backup_path)
        except OSError:
            pass
        raise


def prune_backups(backup_dir: str, max_count: int):
    backups = []
    for path in glob.glob(os.path.join(backup_dir, 'bot.db.backup_*')):
        name = os.path.basename(path)
        try:
            backup_time = datetime.strptime(name, 'bot.db.backup_%Y%m%d_%H%M%S')
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


def main():
    db_path = os.environ.get('DB_PATH', '/data/bot.db')
    backup_dir = os.environ.get('BACKUP_DIR', os.path.dirname(db_path))
    max_count = int(os.environ.get('BACKUP_MAX_COUNT', '14'))

    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'bot.db.backup_{timestamp}')

    try:
        create_backup(db_path, backup_path)
    except Exception as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        sys.exit(1)

    size = os.path.getsize(backup_path)
    print(f"Backup created: {backup_path} ({size} bytes)")

    prune_backups(backup_dir, max_count)


if __name__ == '__main__':
    main()
