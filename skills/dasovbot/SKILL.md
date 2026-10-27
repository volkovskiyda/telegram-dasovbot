---
name: dasovbot
description: Operate the dasovbot Telegram bot through its admin API — status, users, bans, subscriptions, sending videos, VPN state. Use when the user asks to check, fix or change anything about dasovbot or one of its users.
---

Operate dasovbot with the `dasovbot` CLI (repo `telegram-dasovbot`, `dasovbot/cli.py`). It finds the active HA node by itself, sends writes there and prints JSON; you never need the dashboard or ssh for the tasks below.

## Setup (once per machine)

1. `pipx install -e /path/to/telegram-dasovbot` (stdlib-only CLI, Python 3.11+).
2. `~/.config/dasovbot/config.toml` with both node URLs and the admin token:
   ```toml
   urls = ["http://192.168.1.10:8080", "http://192.168.1.20:8080"]
   admin_token = "..."
   ```
   Env `DASOVBOT_URLS` / `DASOVBOT_ADMIN_TOKEN` override per key. The token lives in `data/admin_token.txt` on the nodes (or the `ADMIN_TOKEN` env there); **never paste it into chat, commits or plan docs.**
3. Smoke test: `dasovbot status --pretty` (exit 0 when any node answered; `3` means not configured).
4. Skill symlink (this file): `ln -s "$(pwd)/skills/dasovbot" ~/.claude/skills/dasovbot` from the repo root.

## Rules

- **Reads before writes.** Look the target up (`users --search`, `subscriptions`, `intents --url`) and show the user what you found before changing anything.
- **Every write is `--dry-run` first.** Show the dry-run JSON, then run the real call. Ban/unban/unsubscribe post an audit line to the developer chat; subscribe and send do not.
- **Never `--force`** unless the user named the exact chat id. `--force` only exists to target a numeric chat id the bot has never seen; the normal answer to `unknown chat` is to pick one of the `candidates` with the user.
- **Default target is the admin's own chat** (`DEVELOPER_CHAT_ID`) for `send`, `subscribe`, `unsubscribe`. Use `--to @user` / `--user @user` only when the user names the recipient.
- **Report failures verbatim.** A non-zero exit prints the server's `{"error", "detail", ...}` on stderr; quote it, do not paraphrase. Exit `2` = no (active) node answered; check `status` and tell the user which node is down.
- **What the API cannot do, say so:** it never deletes a subscription for every subscriber at once, never retries an ignored intent, and never changes HA roles (takeover/handback). Those stay on the dashboard.
- Quote results with `--pretty`; the one-line default is for piping.

## Command reference

Generated from `dasovbot --help`; `tests/test_skill_doc.py` fails when it drifts. Refresh with:
`python -c "from dasovbot.cli import render_help; print(render_help())"` and paste the output between the markers.

<!-- cli-help:start -->
```
usage: dasovbot [-h] [--pretty] COMMAND ...

Operate dasovbot through its admin API: finds the active HA node, prints JSON.

positional arguments:
  COMMAND
    status         health, counts, task last-runs, backup age and VPN exit,
                   one block per node
    users          known users, most requests first
    subscriptions  subscriptions with their subscribers
    intents        queued downloads by priority, or one URL's delivery state
    alerts         active health alerts
    playlists      what /subscribe would offer for a video, playlist or
                   channel URL
    ban            ban a user (audited in the developer chat)
    unban          unban a user (audited in the developer chat)
    subscribe      subscribe a chat to a channel feed or playlist
    unsubscribe    remove one chat from a subscription (audited)
    send           download a video to a chat through the intent queue
    populate       run the subscription poll now, for all or one subscription

options:
  -h, --help       show this help message and exit
  --pretty         indent the JSON output

Config: DASOVBOT_URLS (comma-separated) and DASOVBOT_ADMIN_TOKEN, or
~/.config/dasovbot/config.toml with urls = [...] and admin_token = "...". Exit
0 ok, 1 server refused, 2 no (active) node, 3 not configured.

usage: dasovbot status [-h]

options:
  -h, --help  show this help message and exit

usage: dasovbot users [-h] [--search SEARCH] [--banned {true,false}]

options:
  -h, --help            show this help message and exit
  --search SEARCH       match id, name or @username (case-insensitive)
  --banned {true,false}
                        only banned or only unbanned users

usage: dasovbot subscriptions [-h] [--user USER]

options:
  -h, --help   show this help message and exit
  --user USER  only this chat id or @username

usage: dasovbot intents [-h] [--ignored {true,false}] [--url URL]

options:
  -h, --help            show this help message and exit
  --ignored {true,false}
                        only ignored or only live intents
  --url URL             look one URL up: pending, retrying, ignored, delivered
                        or absent

usage: dasovbot alerts [-h]

options:
  -h, --help  show this help message and exit

usage: dasovbot playlists [-h] url

positional arguments:
  url

options:
  -h, --help  show this help message and exit

usage: dasovbot ban [-h] [--dry-run] [--force] target

positional arguments:
  target      numeric id or @username

options:
  -h, --help  show this help message and exit
  --dry-run   resolve and report, write nothing
  --force     accept a numeric chat id the bot has never seen

usage: dasovbot unban [-h] [--dry-run] [--force] target

positional arguments:
  target      numeric id or @username

options:
  -h, --help  show this help message and exit
  --dry-run   resolve and report, write nothing
  --force     accept a numeric chat id the bot has never seen

usage: dasovbot subscribe [-h] [--user USER] [--populate] [--dry-run]
                          [--force]
                          url

positional arguments:
  url          channel (its Videos feed), one of its feeds/playlists, or a
               playlist URL

options:
  -h, --help   show this help message and exit
  --user USER  chat id or @username; default DEVELOPER_CHAT_ID
  --populate   queue the feed's latest videos for that chat now
  --dry-run    resolve and report, write nothing
  --force      accept a numeric chat id the bot has never seen

usage: dasovbot unsubscribe [-h] [--user USER] [--dry-run] [--force] url

positional arguments:
  url          a stored subscription key (see subscriptions)

options:
  -h, --help   show this help message and exit
  --user USER  chat id or @username; default DEVELOPER_CHAT_ID
  --dry-run    resolve and report, write nothing
  --force      accept a numeric chat id the bot has never seen

usage: dasovbot send [-h] [--to TO] [--wait [SECONDS]] [--dry-run] [--force]
                     url

positional arguments:
  url

options:
  -h, --help        show this help message and exit
  --to TO           chat id or @username; default DEVELOPER_CHAT_ID
  --wait [SECONDS]  poll every 5s until delivered, failed or ignored (default
                    600s)
  --dry-run         resolve and report, write nothing
  --force           accept a numeric chat id the bot has never seen

usage: dasovbot populate [-h] [url]

positional arguments:
  url         a stored subscription key; omit for every subscription

options:
  -h, --help  show this help message and exit
```
<!-- cli-help:end -->

## Playbooks

### 1. A user is spamming or abusing the bot

1. `dasovbot users --search <name, @username or id> --pretty` — show the match: `id`, `name`, `username`, `requests`, `last_at`, `banned`. More than one match: ask which.
2. `dasovbot ban <id> --dry-run` — show the `user` block it resolved.
3. `dasovbot ban <id>` — expect `changed: true`.
4. `dasovbot users --search <id>` — confirm `banned: true`. A banned user sees the bot keep "working" (placeholder, then "Video unavailable"); subscriptions keep delivering by design.

### 2. Undo a ban

1. `dasovbot users --banned true --pretty` — list bans with `banned_at`.
2. `dasovbot unban <id>` — `changed: false` means it was not banned.

### 3. A subscription stopped delivering

1. `dasovbot subscriptions --pretty` — find the subscription `url` (the stored key) and its `chat_ids`.
2. `dasovbot intents --url <a recent video url>` — `pending`/`retrying` = queued, `delivered` = done, `absent` = never queued or dropped, `ignored` = dead video.
3. `dasovbot populate <subscription-url>` — polls that feed now (`already_running` means the hourly run is mid-way; wait).
4. Re-check `intents` and `status` (`counts.intents`). Still nothing: `dasovbot playlists <channel-url>` to see whether the channel's feeds moved, then `subscribe <new-url> --dry-run` / `subscribe <new-url>` and `unsubscribe <old-url>`.

### 4. A download is stuck or failed

1. `dasovbot intents --pretty` — by priority; read `status`, `retries`, `retry_in_sec`, `source`, `chat_ids`.
2. `retrying` with `retry_in_sec > 0` is backing off after a transient failure: no action, say when it retries.
3. `ignored` is a dead video (removed, private, age-gated): tell the user; the dashboard's Ignored page can retry or drop it.
4. `pending` for a long time with `queue_size` growing: check `status` (`role`, `tasks.monitor_process_intents`) — the worker only runs on the ACTIVE node.

### 5. Send me this video

1. `dasovbot send <url> --wait` — queues it for the admin chat and polls until `result` is `delivered`, `failed` (the worker gave up), `ignored` (dead video) or `timeout` (10 min; the download continues, check `intents --url` later).
2. `--to @user` only when the user names the recipient; `--dry-run` first when the target is not the admin.

### 6. Subscribe me to a channel

1. `dasovbot playlists <channel-or-video-url> --pretty` — the Videos feed, Streams (when the channel has them) and playlists.
2. Pick with the user (a bare channel URL means its Videos feed; a video URL is refused as ambiguous).
3. `dasovbot subscribe <url> --dry-run` — shows the stored key (`url`) and record.
4. `dasovbot subscribe <url> --populate` — subscribes and queues the latest videos for the chat.

### 7. Is the bot healthy / which node is active / where is the VPN

1. `dasovbot status --pretty` — one block per node. Read `role` (exactly one `active`), `ready`, `lease_holder`, `counts.intents`, `tasks` (last runs), `backup.newest_age_sec`, `vpn.country` / `vpn.ip` / `vpn.last_change_at` / `vpn.changes_since_start` (per node; `vpn: null` = poller not configured there).
2. `dasovbot alerts` — the dashboard's health warnings (stale backups, low disk, missing data).
3. `reachable: false` on a node: it is down or unreachable from here; the other node's `role` tells whether it took over.
