## Telegram Bot

### [dasovbot](https://t.me/dasovbot) is a telegram bot to download and share online video.

#### Based on [python telegram bot](https://github.com/python-telegram-bot/python-telegram-bot) and [yt-dlp](https://github.com/yt-dlp/yt-dlp)

### **[Inline mode:](https://telegram.org/blog/inline-bots)**
`@dasovbot` _video url_ - Download and share video

### **Available commands:**
`/start` - Welcome message

`/download` (`/das`, `/dv`) _video url_ - Download video

`/cancel` - Cancel current operation

`/help` - Show available commands

#### **Subscriptions:**
`/subscriptions` (`/subs`) - Show list of subscriptions

`/subscribe` _playlist url_ - Subscribe to playlist

`/unsubscribe` _playlist url_ - Unsubscribe from playlist

`/playlists` - Show playlists for subscribed channels

`/multiple_subscribe` _playlist urls_ - Bulk subscribe to multiple playlist URLs

### **Web Dashboard**
Password-protected web UI served on `DASHBOARD_PORT` (default 8080).

- **Overview** (`/`) — stats cards, processing queue with remove buttons, populate subscriptions trigger
- **Videos** (`/videos`) — downloaded videos with sorting and source filtering, plus the users who requested each one with ban/unban buttons
- **Ignored** (`/ignored`) — failed/skipped videos with retry and remove actions
- **Subscriptions** (`/subscriptions`) — subscriptions with per-subscriber badges, remove a single subscriber or the whole subscription
- **Users** (`/users`) — users ranked by logged requests, with ban/unban; a user links to the videos they requested. A banned user never gets a new video, but the bot looks like it still works: inline queries go unanswered (the client times out), `/download` shows the loading animation and then `❌ Video unavailable` after 10–60 s, and requests already queued fail the same way on delivery. Subscriptions stay fully usable and keep delivering; only the "show latest videos" shortcut after subscribing is skipped
- **System** (`/system`) — background task status, state sizes, manual subscription polling trigger, and on an HA pair the role card with **Take over** / **Hand back** (see [High availability](#high-availability))

### **JSON API**
Machine-readable endpoints served on the same port under `/api/`, authorized per-request with a bearer token (never the dashboard session cookie). Two tokens: the read-only `API_TOKEN` opens the video library only, the `ADMIN_TOKEN` opens every `/api/` route. Success returns a bare object or array; failure returns `{"error", "detail"}` with a 4xx/5xx status. A rejected bearer is logged once (client IP, method, path) and answered `401 {"error": "unauthorized"}`.

#### Read-only (`API_TOKEN` or `ADMIN_TOKEN`)
Entry key names mirror a sidecar-built library index (`id`, `title`, `channel`, `channelId`, `duration`, `uploadDate`, `tags`, `categories`, `description`, `thumbnail`, `chapters` as `[{start, title}]` with `start` in seconds, `uploadTimestamp` (the upload instant in epoch seconds, `null` when YouTube publishes only a date) and `fetchedAt` in epoch seconds), so index consumers can parse them unchanged; `webpageUrl` and `exported` (file moved to the export folder / media library) are added.

- `GET /api/videos` — all videos with a cached `file_id`, deduplicated by YouTube id, newest upload first. Supports `?exported=true|false` filtering and `ETag`/`If-None-Match` (returns `304 Not Modified` when unchanged). Rows stored before metadata enrichment (no `video_id`) are skipped
- `GET /api/videos/{id}` — a single entry by YouTube video id, `404` when unknown
- `GET /api/openapi.json` — OpenAPI 3.1 description of every `/api` route, readable with either token

```bash
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:8080/api/videos?exported=true
```

#### Admin (`ADMIN_TOKEN`)
The read side of what an operator or agent needs before acting. Served by both nodes of an HA pair (each describes itself).

- `GET /api/status` — the `/health` fields (`role`, `node`, `ready`, `rev`, …) plus `counts` (`users`, `banned`, `videos` deduplicated as the videos API serves them, `videos_rows`, `subscriptions`, `subscribers`, `intents: {pending, retrying, ignored, total}`, `inline_queries`, `alerts`), `tasks` (background task name → last run or `null`), `backup: {dir, newest_age_sec}`, `migration`, `queue_size`, `vpn` (see below; `null` without `VPN_CONTROL_APIKEY`) and `node_time`
- `GET /api/users?search=&banned=true|false` — `[{id, name, username, banned, banned_at, requests, last_at}]`, most requests first; `search` matches id, name and username (with or without `@`) case-insensitively
- `GET /api/subscriptions?chat_id=` — `[{url, title, uploader, uploader_videos, chat_ids: [{id, name}]}]`, optionally only the given subscriber's
- `GET /api/intents?ignored=true|false` — `[{url, status, priority, retries, source, title, upload_date, chat_ids, inline_count, message_count, retry_in_sec}]` by priority; `status` is `pending`, `retrying` (failed before or backing off) or `ignored`
- `GET /api/intents?url=` — one URL's `{url, status, intent}`: the entry above while queued, `delivered` (popped, file cached) or `absent` (never queued or dropped) with `intent: null`
- `GET /api/alerts` — `[{id, level, message, since}]`, the dashboard health banner
- VPN status: with `VPN_CONTROL_APIKEY` set, the bot polls the gluetun control server every 60 s on both nodes and `/api/status` carries `vpn: {ip, country, city, status, last_change_at, changes_since_start, last_poll_ok, last_poll_at}` for *this* host. A reconnect (`last_change_at`, `changes_since_start`) is a public IP that changed between two good polls, or a good poll after a failed one; the first poll after start is the baseline. A failed poll keeps the last known exit and sets `last_poll_ok: false`. In memory only, never synced, no alerts
- `GET /api/playlists?url=` — what `/subscribe` would offer for a video, playlist or channel URL: `{uploader, uploader_url, uploader_videos, playlists: [{title, url}], direct, fallback_videos}`; `400` for a non-http or unsupported URL, `502 {error: "extraction failed", detail}` when yt-dlp fails

Writes take a JSON object body and run only on the ACTIVE node: a passive node answers every `/api` POST with `409 {"error": "passive", "detail", "active": <peer url>}`. Every write accepts `dry_run: true` (same resolution, nothing written, the response says what would happen). `chat_id` defaults to `DEVELOPER_CHAT_ID` and may be a numeric id or `@username`; an unknown numeric id is refused with `404 {"error": "unknown chat", "candidates": [...]}` (the closest known ids) unless `force: true`. `{id}` in a user route is a numeric id or `@username` (`404 unknown user` with candidates, `409 ambiguous user`). Ban, unban and unsubscribe post one audit line to the developer chat (`API: banned …`); subscribe and send do not. Takeover/handback and removing a subscription for all its subscribers stay dashboard-only.

- `POST /api/users/{id}/ban`, `POST /api/users/{id}/unban` — `{action, user, changed}`; `changed: false` when the user already was in that state
- `POST /api/subscriptions {url, chat_id?, populate?, force?}` — the same resolution as `/subscribe`: a channel URL subscribes to its Videos feed, a feed or playlist URL of that channel to that entry, a URL yt-dlp cannot place on a channel as is; a video URL gets `409 {"error": "ambiguous url", "playlists": [...]}`. Response `{action: subscribe|add_subscriber, url, subscription, populate: started|skipped}` (`url` is the stored key, which may differ from the request) or `{status: "already_subscribed", …}`; `populate: true` queues the feed's latest videos for that chat; `502 extraction failed` when yt-dlp fails
- `POST /api/subscriptions/remove {url, chat_id?, force?}` — removes one chat; `{action, url, chat_id, removed_subscription}` (`true` when that was the last subscriber); `404 not subscribed` / `404 chat not subscribed`
- `POST /api/intents {url, chat_id?, force?}` — queue a download for a chat through the intent system (dedup with every other requester of the raw URL, `source: "api"`): `{action: "queue", url, chat_id, cached, intent}`; `403 chat banned`
- `POST /api/populate {url?}` — run the subscription poll now, for every subscription or one stored key: `{status: started|already_running, scope}`

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://localhost:8080/api/status
curl -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
     -d '{"url": "https://www.youtube.com/watch?v=...", "dry_run": true}' http://localhost:8080/api/intents
```

#### CLI
`dasovbot` is a stdlib-only client for the admin API (`dasovbot/cli.py`): it knows both HA nodes, probes `/health`, sends writes to the active node and prints JSON (one line; `--pretty` indents). Installed on its own, without the bot's dependencies:

```bash
pipx install -e .          # from the repo root; Python 3.11+ (tomllib)
dasovbot --help
```

Configuration, env first then file (per key): `DASOVBOT_URLS` (comma-separated) and `DASOVBOT_ADMIN_TOKEN`, or `~/.config/dasovbot/config.toml`:

```toml
urls = ["http://192.168.1.10:8080", "http://192.168.1.20:8080"]
admin_token = "..."
```

| Command | Does |
|---|---|
| `status` | one block per node: `{url, reachable, status}` with that node's `/api/status` (a passive node answers too); exit 0 if any node answered |
| `users [--search X] [--banned true\|false]`, `subscriptions [--user ID]`, `intents [--ignored true\|false] [--url URL]`, `alerts`, `playlists URL` | the read routes, from the first node that answers (active preferred) |
| `ban ID\|@user`, `unban ID\|@user` | `POST /api/users/{id}/ban\|unban` |
| `subscribe URL [--user ID] [--populate]`, `unsubscribe URL [--user ID]` | `POST /api/subscriptions`, `POST /api/subscriptions/remove` |
| `send URL [--to ID] [--wait [SECONDS]]` | `POST /api/intents`; `--wait` polls `GET /api/intents?url=` every 5 s (default 600 s) and prints `{url, result: delivered\|failed\|ignored\|timeout, polls, intent, queued}`; exit 0 only for `delivered` (`failed` = the worker dropped the intent) |
| `populate [URL]` | `POST /api/populate` |

Every write takes `--dry-run` and `--force`. Writes probe every node, go to the active one and retry once on a `409 passive` (the lease moved between the probe and the request). Exit codes: `0` ok, `1` the server refused (its `{error, detail}` on stderr), `2` no node / no active node answered, `3` not configured.

##### Claude Code skill
`skills/dasovbot/SKILL.md` teaches an agent the CLI (its command reference is generated from `dasovbot --help`) and the operator playbooks: spam/abuse → ban, undo a ban, a subscription stopped delivering, a stuck download, "send me this video", subscribe to a channel, health/VPN. Link it into Claude Code once per machine, and refresh the embedded reference whenever a flag changes (`tests/test_skill_doc.py` fails until you do):

```bash
ln -s "$(pwd)/skills/dasovbot" ~/.claude/skills/dasovbot
python -c "from dasovbot.cli import render_help; print(render_help())"   # paste between the cli-help markers
```

### **Configuration:**
- Copy `.env.example` file to `.env` and change `READ_TIMEOUT`, `BASE_URL`, `BOT_TOKEN`, `DEVELOPER_CHAT_ID` and `LOADING_VIDEO_ID` environment variables.
- `READ_TIMEOUT` variable sets the waiting timeout for bot requests
- `BASE_URL` and `BOT_TOKEN` environment variables used to initialize bot.
- For `BASE_URL` you can use standard `https://api.telegram.org/bot` or use a local server ([tutorial](https://github.com/tdlib/telegram-bot-api)).
- When the local server is started with `--local`, also set `LOCAL_MODE=true`: videos are then handed to the server by file path (`file://` URI) instead of being read into bot memory — required for multi-GB uploads. The server must see the media folder at the same absolute path as the bot (`docker-compose.yml` mounts `./config/media/` at `/media` in both containers).
- Obtain `BOT_TOKEN` via @BotFather ([tutorial](https://core.telegram.org/bots/tutorial#obtain-your-bot-token))
- `Tip`: Turn inline mode on, edit inline placeholder and set inline feedback to 100% in bot settings.
- More info at [official github repository](https://github.com/tdlib/telegram-bot-api)
- `DEVELOPER_CHAT_ID` and `LOADING_VIDEO_ID` environment variables are used to populate loading animation
- For local server you can use [docker telegram bot api image](https://github.com/volkovskiyda/docker-telegram-bot-api)

#### **Environment variables:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | Yes | | Telegram bot token from @BotFather |
| `BASE_URL` | Yes | | Telegram Bot API base URL |
| `DEVELOPER_CHAT_ID` | Yes | | Chat ID for developer notifications |
| `DEVELOPER_ID` | No | `DEVELOPER_CHAT_ID` | Developer user ID for export permissions |
| `READ_TIMEOUT` | No | `30` | Request timeout in seconds |
| `LOCAL_MODE` | No | `false` | Pass uploads to the Bot API server as `file://` paths instead of multipart bodies. Requires a server started with `--local` that sees the media folder at the same path |
| `BASE_FILE_URL` | No | derived from `BASE_URL` | Bot API file-download base URL (`.../file/bot`) |
| `UPLOAD_CONCURRENCY` | No | `1` | Max simultaneous video-file uploads; keep at `1` to bound memory and IO |
| `LOADING_VIDEO_ID` | No | | Video URL used for loading animation |
| `ANIMATION_FILE_ID` | No | | Pre-cached animation file ID (skips loading upload) |
| `CONFIG_FOLDER` | No | `./config` | Root folder for data/media/export directories. Docker deployments must set `/` so data lands on the mounted `/data`, `/media`, `/export` volumes (docker-compose.yml does) |
| `EMPTY_MEDIA_FOLDER` | No | `false` | Clear the media folder when the intent worker crashes and restarts |
| `DISK_FREE_MIN_GB` | No | `20` | Free space below which the media, export or data volume raises a health alert (checked hourly); `0` disables |
| `DASHBOARD_PASSWORD` | No | | Password for web dashboard access (auto-generated if not set; written to `data/dashboard_password.txt`). Required, with the same value, on both nodes of an HA pair |
| `DASHBOARD_PORT` | No | `8080` | Port for web dashboard server |
| `DASHBOARD_BEHIND_PROXY` | No | `false` | Set `true` when the dashboard sits behind a reverse proxy (Traefik, nginx, …): login rate limiting uses the client IP from `X-Forwarded-For` (plus a 5× larger budget per socket peer, so a client that reaches the port without the proxy cannot rotate the header past the limit), and the session cookie is marked `Secure` when the proxy reports HTTPS via `X-Forwarded-Proto` |
| `API_TOKEN` | No | | Read-only bearer token for `/api/videos` (auto-generated if not set; written to `data/api_token.txt`). Required, with the same value, on both nodes of an HA pair |
| `ADMIN_TOKEN` | No | | Bearer token for the admin API, accepted on every `/api/` route (auto-generated if not set; written to `data/admin_token.txt`). Same value on both nodes of an HA pair; `API_TOKEN` cannot call admin routes |
| `COOKIES_FILE` | No | | Path to cookies file for yt-dlp |
| `BACKUP_CRON` | Docker | | Cron schedule for automatic SQLite backups (`entrypoint.sh` installs it into cron; empty disables). docker-compose defaults it to `0 */12 * * *` |
| `BACKUP_MAX_COUNT` | Docker | `14` | Backups kept by `backup.py`; older ones are pruned |
| `DB_PATH` | Docker | `/data/bot.db` | Database path `backup.py` reads from |
| `BACKUP_DIR` | Docker | folder of `DB_PATH` | Folder `backup.py` writes backups to |
| `TELEGRAM_API_ID` | Docker | | Telegram API ID (for local Bot API server) |
| `TELEGRAM_API_HASH` | Docker | | Telegram API hash (for local Bot API server) |
| `NODE_ROLE` | HA | `primary` | `primary` (reclaims the lease after `FAILBACK_STABLE_SEC`) or `standby` |
| `NODE_NAME` | HA | hostname | Name shown in `/health`, heartbeats and notifications |
| `PEER_URL` | HA | | Dashboard URL of the *other* node by static LAN IP, e.g. `http://192.168.1.10:8080`. Blank = single node (HA off). Never a hostname (does not resolve inside gluetun) or a floating IP |
| `SYNC_SECRET` | HA | | Shared bearer secret for `/sync/*`; identical on both nodes, separate from `API_TOKEN`. Blank = HA off |
| `HEARTBEAT_INTERVAL_SEC` | HA | `10` | Passive node's heartbeat and feed-pull period |
| `LEASE_TTL_SEC` | HA | `30` | No successful heartbeat for this long = lease lost, the standby takes over. Must be ≥ `HEARTBEAT_INTERVAL_SEC` |
| `FAILBACK_STABLE_SEC` | HA | `180` | Continuous healthy heartbeats the returning primary needs before it asks for the lease back |
| `BACKUP_SKIP_ROLE_CHECK` | Docker | `false` | `true` makes `backup.py` back up even on a passive node (manual replica snapshot) |
| `VPN_CONTROL_APIKEY` | No | | Key of a gluetun control-server auth role that may `GET /v1/publicip/ip` and `GET /v1/vpn/status`; blank = no VPN poller and `vpn: null` in `/api/status` |
| `VPN_CONTROL_URL` | No | `http://127.0.0.1:8000` | gluetun control server, reachable from the bot (shared network namespace when the bot runs inside the VPN container) |

### **Project structure:**
```
dasovbot/              # Main package
  __main__.py          # Entry point
  config.py            # Config loading, ydl_opts
  constants.py         # Error messages, timeouts, states
  models.py            # Dataclasses for video, intent, subscription
  database.py          # SQLite persistence (aiosqlite)
  persistence.py       # File utilities (remove, empty media)
  state.py             # BotState (mutable state container, write-through DB)
  downloader.py        # yt-dlp wrapper
  helpers.py           # Shared utilities
  cli.py               # `dasovbot` CLI for the admin API (stdlib only, installed via pyproject.toml)
  handlers/            # Telegram handler modules
  services/            # Background tasks and intent processing
    ha.py              # Role controller (active/standby state machine) and PtbRunner
    sync.py            # Sync client: heartbeat, change-feed pull, snapshot; developer notifier
    vpn.py             # VPN status poller against the gluetun control server
  dashboard/           # Web dashboard (aiohttp, jinja2, session auth)
    api.py             # Read-only JSON API (/api/videos, API_TOKEN)
    admin_api.py       # Admin JSON API (status, users, subscriptions, intents, alerts, playlists, writes; ADMIN_TOKEN)
    openapi.py         # OpenAPI 3.1 description served at /api/openapi.json
    sync.py            # /health and the peer-facing /sync/* endpoints
main.py                # Thin wrapper entry point
pyproject.toml         # Packaging for the CLI only (`pipx install -e .`); the bot installs requirements.txt
info.py                # CLI: video info lookup
subscriptions.py       # CLI: bulk subscription management
empty_media_folder.py  # CLI: clear media folder
backup.py              # CLI: SQLite online backup
preview_dashboard.py   # CLI: start the dashboard with mock data
skills/dasovbot/       # Claude Code skill: CLI reference (generated from --help) + operator playbooks
conftest.py            # Pytest config (silences PTB warnings)
run_tests.sh           # Full test suite runner (unit + integration + E2E)
entrypoint.sh          # Docker entrypoint (cron + bot; backup schedule from BACKUP_CRON)
```

### **Architecture**

**Entry flow:** `main.py` → `dasovbot/__main__.py` → loads config from env vars → initializes yt-dlp → opens SQLite database → starts the dashboard → loads persisted state → builds Telegram Application → registers handlers → starts the role controller, which starts polling and the background tasks only while the node is ACTIVE (a single node is ACTIVE at once; see [High availability](#high-availability)).

**State management:** Central `BotState` dataclass (`state.py`) holds all mutable state: video cache, intents, subscriptions, users, download queue (`asyncio.Queue`). State is accessed via `context.bot_data['state']` in handlers. Changes are persisted immediately (write-through) to a SQLite database (`{CONFIG_FOLDER}/data/bot.db`) via `database.py`; every write stamps a per-node revision (`rev`) and every delete leaves a tombstone, which is what the HA change feed replicates. On first run, existing JSON files are automatically migrated to SQLite.

**Intent system:** Video download requests are modeled as `Intent` objects (not processed immediately). Intents accumulate `chat_ids` and `inline_message_ids` from multiple requesters, with priority based on requester count. A background worker (`intent_processor.py`) processes the queue in priority order — this deduplicates downloads when multiple users request the same video.

**Handler registration:** All handlers registered in `handlers/__init__.py:register_handlers()`. Updates are processed concurrently (`MAX_CONCURRENT_UPDATES`): the download and inline handlers await a yt-dlp metadata extraction, and PTB's default serial queue let one slow URL stall every other user. Multi-step flows (download, subscribe, unsubscribe) use `ConversationHandler` with states defined in `constants.py`.

**Background tasks:** Started in `services/background.py:start_background_tasks()` via `asyncio.create_task`:
- Loading-animation population
- Subscription polling (hourly)
- Intent queue processing
- Inline query cache cleanup
- Backup freshness monitoring (alerts the developer if backups stop)
- Media folder sweep (hourly; removes leftover files older than 6 hours)
- Disk space monitoring (hourly; a dashboard alert and one developer message per episode when the media, export or data volume has less than `DISK_FREE_MIN_GB` free)

The web dashboard is started separately in `__main__.py` (`start_dashboard`) before the Telegram application is built.

**Video processing pipeline:**
1. User sends URL → handler creates an `Intent` (download request)
2. Background task `monitor_process_intents` picks up intents from an `asyncio.Queue`
3. `intent_processor.py` extracts metadata and downloads via yt-dlp (blocking calls run in executor). Each download is a `DownloadAttempt`: on timeout it is cancelled at yt-dlp's next progress callback, and a failed or cancelled attempt deletes everything it wrote (`.part`, fragments, merged output)
4. Non-MP4 videos (MKV, WebM, etc.) are converted to MP4 via ffmpeg — fast remux first, transcode fallback
5. Video posted to Telegram, `file_id` cached for future reuse. With `LOCAL_MODE=true` the bot sends only the file path (`file:///media/...`) and the Bot API server reads the bytes from the shared media volume — the video never passes through bot memory. Sends that upload an actual file hold `state.upload_semaphore` (`UPLOAD_CONCURRENCY`, default 1), so the intent worker, the retry fallback, and background tasks never upload concurrently

**Models:** All domain objects (`models.py`) are dataclasses with manual `to_dict()`/`from_dict()` serialization (stored as JSON within SQLite) — no ORM or external serialization library.

**Key modules:**
- `handlers/` — Telegram command and inline query handlers (`download.py`, `inline.py`, `subscription.py`, `common.py`)
- `services/background.py` — Hourly subscription polling, intent queue processing, inline cache cleanup, media folder sweep
- `services/intent_processor.py` — Download execution and Telegram posting
- `downloader.py` — yt-dlp wrapper with `asyncio.Lock` for synchronized access, MP4 conversion via ffmpeg
- `dashboard/` — aiohttp web server with cookie-based session auth, jinja2 templates, overview, videos, ignored, and system pages

**Subscriptions:** Playlist URLs mapped to subscriber chat IDs. Background task polls hourly, creates intents for new videos.

**Video caching:** `VideoInfo` objects cached by URL in `state.videos`. Once a video has a Telegram `file_id`, it's served instantly without re-downloading.

**Error classification:** Video extraction errors are matched against `VIDEO_ERROR_MESSAGES` in `constants.py` to distinguish user-facing errors from internal failures. Errors matching `TRANSIENT_ERROR_MESSAGES` (YouTube's bot check, an expired or throttled media URL, 429/5xx) are waited out instead: the intent backs off for `INTENT_TRANSIENT_BACKOFF_SEC` (15 min) without spending one of its `MAX_INTENT_RETRIES`, up to `MAX_TRANSIENT_RETRIES` times in a row, before it counts as a real failure.

### **System dependencies:**
- Python 3.11+ (yt-dlp 2026.10.26 dropped 3.10; the Docker image runs 3.12)
- [ffmpeg](https://ffmpeg.org/) — required for video conversion and yt-dlp post-processing
- [Deno](https://deno.com/) — JavaScript runtime required by yt-dlp for YouTube extraction (`brew install deno`)

### **Run:**
- Install requirements
```bash
pip install -r requirements.txt
```
- Run the bot
```bash
python main.py
```

- Show info
```bash
python info.py '<url>'
```
Pass the `-d` (`--download`) flag to also download the video
```bash
python info.py '<url>' -d
```

### **Tests:**

Install the test dependencies first (pytest + pytest-asyncio, on top of the app requirements):
```bash
pip install -r requirements-test.txt
```

#### Unit tests
No bot token or external services required.
```bash
python -m pytest tests --ignore=tests/integration
```

#### Run a specific test file
```bash
python -m pytest tests/test_database.py -v
```

#### Run a specific test class or method
```bash
python -m pytest tests/test_state.py::TestSetVideo -v
python -m pytest tests/test_helpers.py::TestRemoveCommandPrefix::test_strips_command -v
```

#### Integration tests
Requires `.env.test` with test bot credentials. See `tests/integration/README.md` for setup.
```bash
cp .env.test.example .env.test   # fill in test bot token and user ID
python -m pytest tests/integration -v
```

#### All tests (unit + integration, requires `.env.test`)
```bash
python -m pytest tests -v
```

#### Full suite via script (unit + integration + downloads + E2E)
```bash
./run_tests.sh           # all stages
./run_tests.sh --no-e2e  # skip the manual E2E stage
```

The script runs four stages in order and stops on the first failure:

1. **Unit tests** — no credentials or network needed
2. **Integration tests** — real yt-dlp extraction against YouTube and real Telegram API calls, including an actual download/upload of `TEST_VIDEO_URL` (keep it a short clip)
3. **LOCAL_MODE uploads** — the intent pipeline hands the video to a local Bot API server as a `file://` path; skipped automatically when the server is unreachable
4. **Manual E2E tests** — always last, because they **require user interaction**: the script prints a banner and waits for confirmation, then the test bot messages you in Telegram to send `/start` (30 s window) and to run an inline query and tap the result (120 s window). Needs inline mode and 100% inline feedback enabled via @BotFather (`/setinline`, `/setinlinefeedback`). Skipped when the terminal is non-interactive or with `--no-e2e`.

Required in `.env.test` (copy from `.env.test.example`):

| Variable | Required | Description |
|---|---|---|
| `TEST_BOT_TOKEN` | Yes | Test bot token from @BotFather (use a separate bot, not production) |
| `TEST_USER_ID` | Yes | Your Telegram user ID (from @userinfobot) |
| `TEST_CHAT_ID` | No | Chat for test messages; defaults to `TEST_USER_ID` |
| `TEST_VIDEO_URL` | Yes | A real, **short** video URL — it is downloaded and uploaded for real |
| `TEST_BASE_URL` | No | Local Bot API server URL; when unreachable, tests fall back to the official API with a warning |
| `TEST_CHANNEL_URL` | No | Channel for subscription tests (defaults to the channel that owns `TEST_VIDEO_URL`) |
| `TEST_PLAYLIST_URLS` | No | Comma-separated real playlist URLs for subscription tests: each is polled with flat metadata only; with downloads enabled, one test downloads the **shortest** entry (skipped if nothing is under 5 min) |

The script sets `TEST_ENABLE_DOWNLOAD`, `TEST_LOCAL_MODE`, and `ENABLE_E2E_TESTS` itself per stage, so they don't need to be set in `.env.test`.

##### Local Bot API server for tests
Stage 3 (and testing against `TEST_BASE_URL` in general) needs a local [telegram-bot-api](https://github.com/volkovskiyda/docker-telegram-bot-api) server started with `--local` that mounts `/tmp/test_config/media` at the same absolute path — that is where the tests write downloaded media, and in local mode the server reads the uploads from that folder by `file://` path. Get `api_id`/`api_hash` at [my.telegram.org](https://my.telegram.org):

```bash
docker run -dit --rm --name telegram-bot-api \
  -e TELEGRAM_API_ID=<api_id> -e TELEGRAM_API_HASH=<api_hash> \
  -v /tmp/test_config/media:/tmp/test_config/media \
  -p 8081:8081 \
  ghcr.io/volkovskiyda/telegram-bot-api --local
```

Then set `TEST_BASE_URL=http://<host>:8081/bot` in `.env.test`.

### **Docker container**

```bash
docker run -dit --rm --name telegram --pull=always -e TELEGRAM_API_ID=<api_id> -e TELEGRAM_API_HASH=<api_hash> -v $PWD/config/media:/media -p 8081:8081 ghcr.io/volkovskiyda/telegram-bot-api --local ; docker run -dit --rm --name dasovbot --pull=always -e READ_TIMEOUT=30 -e BASE_URL=http://host.docker.internal:8081/bot -e LOCAL_MODE=true -e CONFIG_FOLDER=/ -e BOT_TOKEN=<your_bot_token> -e LOADING_VIDEO_ID=<loading_animation_video_url> -e DEVELOPER_CHAT_ID=<developer_chat_id> -v $PWD/config/data:/data -v $PWD/config/media:/media ghcr.io/volkovskiyda/dasovbot
```
##### **Note**: change `<api_id>`, `<api_hash>`, `<your_bot_token>`, `<loading_animation_video_url>` and `<developer_chat_id>`. Both containers mount the same media folder at `/media` so the api server (running with `--local`) can read the files the bot passes by path. The `/data` mount keeps the SQLite database outside the (`--rm`) container — without it the database is lost when the container is removed.

### **Docker compose**
##### **Note**: Populate `.env` based on `.env.example`. See [Configuration](#configuration) for details
#### Change `BASE_URL` in `.env`:
`BASE_URL=http://api:8081/bot`
```bash
docker compose up -d
```

#### **Database backup:**
```bash
docker exec dasovbot python backup.py
```

### **High availability**

Two nodes run the same image — a `primary` (the Proxmox LXC) and a `standby` (the Raspberry Pi) — and the bot survives the primary host going down. Both processes stay up all the time; a **role controller** (`services/ha.py`) decides which one talks to Telegram. HA is off unless both `PEER_URL` and `SYNC_SECRET` are set; without them a node is standalone and behaves exactly as a single deployment.

#### Roles and lease

```
            lease lost / cold start /                 handoff requested          peer reports
            handoff drained                           by the peer                ACTIVE
  PASSIVE ─────────────────────────────▶ ACTIVE ─────────────────────▶ DRAINING ─────────────▶ PASSIVE
     ▲                                     │
     └──── 409 Conflict (standby only) ────┘
```

- **ACTIVE** polls Telegram and runs every background task (subscriptions, intent worker, inline cache cleanup, media sweep, backup monitoring). **PASSIVE** runs only the dashboard (read-only), `/health` and the sync client. **DRAINING** has stopped polling and every background task; the download or upload that was in flight is abandoned (its files are removed) and the intent is retried by the node that takes over.
- The passive node calls the active node's `/sync/heartbeat` every `HEARTBEAT_INTERVAL_SEC` and pulls the change feed after each reply. No successful heartbeat for `LEASE_TTL_SEC` = lease lost → the standby becomes ACTIVE, if it is **ready** (its last sync is within 3 × `LEASE_TTL_SEC` of the last heartbeat it saw; otherwise it stays passive, alerts the developer and keeps trying).
- **Cold start** with no peer in sight: the primary claims after one `HEARTBEAT_INTERVAL_SEC`, the standby after `LEASE_TTL_SEC + HEARTBEAT_INTERVAL_SEC`.
- **Failback:** the returning primary starts PASSIVE, syncs, and after `FAILBACK_STABLE_SEC` of continuous healthy heartbeats requests the lease back (`POST /sync/handoff`). The standby stops polling and cancels every background task at once (nothing waits for the download in progress), answers `drained: true`, the primary pulls the final changes and becomes ACTIVE; the standby goes PASSIVE when it sees that. A drained node whose peer never activates within `LEASE_TTL_SEC` re-activates itself: nothing is ever left with nobody polling. The requesting node then treats its handoff as lapsed and asks again after `LEASE_TTL_SEC`. On activation a node first deletes the unfinished downloads (`*.part`, `*.ytdl`, fragments, temp files) left in its media folder.
- **Split-brain breakers:** a standby that is ACTIVE and sees the primary ACTIVE in a heartbeat steps down; a standby that gets Telegram's `409 Conflict` steps down at once (the primary logs it and keeps polling). Telegram's 409 only fires between pollers of the *same* Bot API server, so the heartbeat is the real guard: if the LAN between the hosts fails while both are up, both will poll until it heals (accepted). **Hold-down:** after a step-down the node refuses the next automatic *lease lost* takeover for 2 × `LEASE_TTL_SEC`, doubling per consecutive step-down up to 15 min, so a flapping link costs one short split-brain window per hold-down instead of one per TTL; `FAILBACK_STABLE_SEC` of uninterrupted heartbeats clears the backoff. Handoffs and **Take over** are not held down (the peer, or the operator, asked for it).
- **Backlog on activation:** every local Bot API server receives every update and buffers it while nobody polls it, so the first poll after activation returns everything the other node already handled. The activating node drains that backlog first and keeps only messages dated after the peer was last seen ACTIVE (minus one heartbeat interval); inline and callback queries in the backlog are stale and dropped. Duplicates are bounded to about one heartbeat interval; nothing is silently lost.
- **Manual override:** `/system` shows a role card with **Take over** (passive node; readiness still enforced) and **Hand back** (active node; on the primary the button reads *Hand over to standby*). A manual takeover of the standby sets a persisted **hold**: automatic failback is suspended until Hand back is clicked on either node.
- **Notifications:** the developer chat gets every transition (🟢 active, ⏳ handoff requested, 🟡 drained, ⚪ handed over, 🔴 stepped down, ⚠️ cannot take over) and sync errors at most once per 10 minutes. An identical transition repeated within 10 minutes is not re-sent; the next copy that goes out carries the collapsed count (`+3 collapsed in the last 10 min`). Every transition (collapsed ones included) and every error notice that was sent is also kept per node in `sync_meta` (`ha:events`, last 200) and listed newest-first on `/system` as **HA history** — container logs disappear with the container, this does not.

#### Data sync

The database is replicated by the app, **not** by Syncthing. Every write on the active node stamps the row with a revision from a per-node Lamport-style counter (`sync_meta`); deletes write a `tombstones` row. The passive node pulls `GET /sync/changes?since=<rev>` in pages of 500 ordered by revision and applies rows and tombstones into SQLite and memory through `apply_remote_*` paths that never trigger side effects (no downloads, no messages). Once an hour it pulls `GET /sync/snapshot` (a SQLite backup file) and reconciles against it as a self-healing floor; a fresh standby bootstraps from the snapshot. A snapshot whose SHA-256 does not match `X-Dasovbot-Sha256`, that fails `PRAGMA quick_check`, or that holds a row whose JSON does not parse is rejected before it touches the local database (`sync error: snapshot rejected: …`), and the next tick pulls it again.

When the primary returns after a takeover, the standby wins on every key it touched since the handoff point (its revisions replace the primary's, its tombstones delete), `requests` rows merge by append (unique on user, url, time), and the primary's surviving unseen rows are re-stamped with fresh revisions so they flow back to the standby. The standby keeps its feed cursor across the handback instead of jumping it to its own counter: the re-stamps happened while the standby was still writing, so they sort below that counter, and the rows the standby wrote while active come back once through the feed as no-ops. Up to `HEARTBEAT_INTERVAL_SEC` of the primary's last writes may be invisible to the standby at takeover; they come back when the primary returns.

**Not synced:** the in-memory inline-query cache (users simply re-query), `animation_file_id` (set `ANIMATION_FILE_ID` on both nodes or each activation re-uploads the animation once), dashboard sessions (log in again on the other node), health alerts, the `/media` working folder. `/export` is replicated by Syncthing; its lag never blocks a takeover.

#### Endpoints

`/health` is public; `/sync/*` require `Authorization: Bearer <SYNC_SECRET>` (never the API token or a session).

| Endpoint | Role | Request | Response |
|---|---|---|---|
| `GET /health` | any | — | `200 {role, node, lease_holder, last_sync_rev, last_sync_at, ready, peer, peer_url, node_role, enabled, rev, manual_hold, handback_requested, drained}`; a node without HA reports `role: active`, `ready: true` |
| `GET /sync/heartbeat` | any | — | `200 {node, role, lease_until, rev, drained, manual_hold, handback_requested, handoff_rev}`; `503` when HA is off |
| `GET /sync/changes` | any | `?since=<rev>&limit=<n≤500>` | `200 {since, until, has_more, rows: {videos, intents, users, subscriptions, banned_users, requests}, tombstones}`; `400` on bad params |
| `GET /sync/snapshot` | any | — | `200` SQLite file (`application/x-sqlite3`, headers `X-Dasovbot-Rev`, `X-Dasovbot-Sha256`) |
| `POST /sync/handoff` | active | `{"manual": bool, "node": name}` | `202 {accepted, role: draining, drained: false}` on ACTIVE, `200` with the current `drained` when already draining, `409 {error}` when passive or when the primary is asked for an automatic handoff |

Force a takeover from a shell (run against the **active** node, then watch `/health` on the other):

```bash
curl -X POST -H "Authorization: Bearer $SYNC_SECRET" -H "Content-Type: application/json" \
     -d '{"manual": true}' http://192.168.1.10:8080/sync/handoff
curl http://192.168.1.20:8080/health
```

#### Syncthing

The app never syncs files. `/data` (folder `dasovbot`) and `/export` (folder `Telegram`) are shared between the hosts by Syncthing, so the hot database must be excluded or the passive replica can overwrite the live file:

```
# .stignore for the dasovbot folder (/data) — bot.db.backup_* stays synced
bot.db
bot.db-wal
bot.db-shm
*.db-journal*
dashboard_password.txt
api_token.txt
```
```
# .stignore for the Telegram folder (/export): exported videos land as <name>.partial
# and are renamed into place atomically
**/*.partial
```

**Both `.stignore` files must be in place on both Syncthing instances before the standby bot is started for the first time.**

#### Backups

Only the active node's cron runs `backup.py`: it asks `http://127.0.0.1:$DASHBOARD_PORT/health` and exits 0 (skipped) when the node is passive or the bot is not answering, so two cron jobs never prune each other's files in the shared `/data`. `BACKUP_SKIP_ROLE_CHECK=true` (env, or `docker exec dasovbot env BACKUP_SKIP_ROLE_CHECK=true python backup.py`) forces a backup of a passive replica. `monitor_backups` alerts only on the active node. The `subscriptions.py` CLI asks the same `/health` and refuses to write unless the node is active (rows written on a passive node are overwritten by the next sync); `--skip-role-check` writes anyway, e.g. while the bot is stopped.

#### Addressing

Each node keeps its own dashboard address; the passive dashboard shows a banner linking to the active node, and `/health` tells you who is active. There is no floating IP. `PEER_URL` is the peer's **static LAN IP** (`http://192.168.1.10:8080` on the standby, `http://192.168.1.20:8080` on the primary; the Pi has a reserved DHCP lease): both bots run inside gluetun, whose built-in DNS-over-TLS resolver ignores Pi-hole and the router, so LAN hostnames never resolve inside the container. gluetun on both hosts needs `FIREWALL_OUTBOUND_SUBNETS=192.168.1.0/24` so the bot can reach the peer, and each host its own WireGuard key (most providers allow one session per key).

#### Rollout

1. Put the `.stignore` files above on both Syncthing instances.
2. On the standby, delete the stale replica (`bot.db`, `bot.db-wal`, `bot.db-shm` under `/data`); the standby bootstraps from the primary's snapshot.
3. Primary: set `NODE_ROLE=primary`, `NODE_NAME`, `PEER_URL=http://192.168.1.20:<port>`, `SYNC_SECRET`, plus explicit `DASHBOARD_PASSWORD`, `API_TOKEN` and `ADMIN_TOKEN`; `docker compose up -d` (`vpn → api → bot`); `curl /health` → `role: active`.
4. Standby: the same with `NODE_ROLE=standby`, `NODE_NAME=rpi`, `PEER_URL=http://192.168.1.10:<port>`, the same `SYNC_SECRET`/`DASHBOARD_PASSWORD`/`API_TOKEN`/`ADMIN_TOKEN`; `docker compose up -d`; watch `/health` go `role: passive`, `ready: true` after the bootstrap snapshot.
5. Rehearse once: Take over on the standby's `/system`, send the bot a link, Hand back, confirm the primary is ACTIVE again and the video shows on both dashboards.

**Rollback:** blank `PEER_URL` on both nodes and restart — each node is standalone again (stop the standby's bot, or both will poll).

**Reading the state:** `curl http://<node>:8080/health` (`role`, `lease_holder`, `ready`, `last_sync_at`); the `/system` card shows the same plus the peer's role, heartbeat age and sync revision; the developer chat messages above mark every transition.
