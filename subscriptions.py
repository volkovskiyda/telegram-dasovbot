#!/usr/bin/env python3
"""Bulk-add subscriptions for a user directly into bot.db.

Reads urls (one per line) from the new-subscriptions file and inserts or
updates rows in the subscriptions table. The bot caches subscriptions in
memory, so restart it (or wait for a redeploy) to pick up the changes.
"""

import argparse
import json
import sqlite3

import yt_dlp

from dasovbot.config import load_config, make_ydl_opts


def add_subscription(ydl, db: sqlite3.Connection, chat_id: str, url: str):
    videos = check_subscription(db, chat_id, f"{url}/videos")
    streams = check_subscription(db, chat_id, f"{url}/streams")
    if videos or streams:
        return

    try:
        info = ydl.extract_info(url, download=False)
    except Exception:
        print(f"# subscribe_playlist failed: {url}")
        return

    uploader_url = info.get('uploader_url')
    uploader = info.get('uploader') or info.get('uploader_id') or ''
    title = info.get('title') or uploader or url
    uploader_videos = f"{uploader_url}/videos"

    data = {
        'chat_ids': [chat_id],
        'title': title,
        'uploader': uploader,
        'uploader_videos': uploader_videos,
    }
    db.execute(
        "INSERT OR REPLACE INTO subscriptions (key, data) VALUES (?, ?)",
        (uploader_videos, json.dumps(data)),
    )
    print(f"New subscription to {title} ({uploader})")


def check_subscription(db: sqlite3.Connection, chat_id: str, url: str) -> bool:
    row = db.execute("SELECT data FROM subscriptions WHERE key = ?", (url,)).fetchone()
    if not row:
        return False
    data = json.loads(row[0])
    chat_ids = data.get('chat_ids') or []
    subscription_info = f"[{data.get('title')}]({url})"
    if chat_id in chat_ids:
        print(f"Already subscribed to {subscription_info}")
    else:
        chat_ids.append(chat_id)
        data['chat_ids'] = chat_ids
        db.execute("UPDATE subscriptions SET data = ? WHERE key = ?", (json.dumps(data), url))
        print(f"Subscribed to {subscription_info}")
    return True


def main() -> None:
    # Built lazily: load_config() requires a populated .env
    config = load_config()

    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--user', help='User chat id')
    parser.add_argument('-d', '--database', help='SQLite database file', default=config.db_file)
    parser.add_argument('-n', '--new', help='File with new subscription urls', default=f'{config.config_folder}/data/new_subscriptions.txt')
    args = parser.parse_args()

    if not args.user:
        parser.print_help()
        return

    with open(args.new) as file:
        lines = [line.strip() for line in file]

    ydl = yt_dlp.YoutubeDL(make_ydl_opts(config))
    db = sqlite3.connect(args.database)
    try:
        for line in lines:
            if line:
                add_subscription(ydl, db, args.user, line)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
