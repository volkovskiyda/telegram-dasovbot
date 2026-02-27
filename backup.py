#!/usr/bin/env python3
"""Create a timestamped backup of bot.db using SQLite's online backup API."""

import os
import sqlite3
import sys
from datetime import datetime


def main():
    db_path = os.environ.get('DB_PATH', '/data/bot.db')
    backup_dir = os.environ.get('BACKUP_DIR', os.path.dirname(db_path))

    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'bot.db.backup_{timestamp}')

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    src.backup(dst)
    dst.close()
    src.close()

    size = os.path.getsize(backup_path)
    print(f"Backup created: {backup_path} ({size} bytes)")


if __name__ == '__main__':
    main()
