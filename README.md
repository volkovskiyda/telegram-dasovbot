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
Machine-readable video metadata served on the same port under `/api/`, authorized per-request with `Authorization: Bearer <API_TOKEN>` (never the dashboard session cookie). Entry key names mirror a sidecar-built library index (`id`, `title`, `channel`, `channelId`, `duration`, `uploadDate`, `tags`, `categories`, `description`, `thumbnail`, `chapters` as `[{start, title}]` with `start` in seconds, `fetchedAt` in epoch seconds), so index consumers can parse them unchanged; `webpageUrl` and `exported` (file moved to the export folder / media library) are added.

- `GET /api/videos` — all videos with a cached `file_id`, deduplicated by YouTube id, newest upload first. Supports `?exported=true|false` filtering and `ETag`/`If-None-Match` (returns `304 Not Modified` when unchanged). Rows stored before metadata enrichment (no `video_id`) are skipped
- `GET /api/videos/{id}` — a single entry by YouTube video id, `404` when unknown

```bash
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:8080/api/videos?exported=true
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
| `DASHBOARD_PASSWORD` | No | | Password for web dashboard access (auto-generated if not set; written to `data/dashboard_password.txt`). Required, with the same value, on both nodes of an HA pair |
| `DASHBOARD_PORT` | No | `8080` | Port for web dashboard server |
| `DASHBOARD_BEHIND_PROXY` | No | `false` | Set `true` when the dashboard sits behind a reverse proxy (Traefik, nginx, …): login rate limiting uses the client IP from `X-Forwarded-For`, and the session cookie is marked `Secure` when the proxy reports HTTPS via `X-Forwarded-Proto` |
| `API_TOKEN` | No | | Bearer token for the JSON API under `/api/` (auto-generated if not set; written to `data/api_token.txt`). Required, with the same value, on both nodes of an HA pair |
| `COOKIES_FILE` | No | | Path to cookies file for yt-dlp |
| `BACKUP_CRON` | Docker | | Cron schedule for automatic SQLite backups (`entrypoint.sh` installs it into cron; empty disables). docker-compose defaults it to `0 */12 * * *` |
| `BACKUP_MAX_COUNT` | Docker | `14` | Backups kept by `backup.py`; older ones are pruned |
| `DB_PATH` | Docker | `/data/bot.db` | Database path `backup.py` reads from |
| `BACKUP_DIR` | Docker | folder of `DB_PATH` | Folder `backup.py` writes backups to |
| `TELEGRAM_API_ID` | Docker | | Telegram API ID (for local Bot API server) |
| `TELEGRAM_API_HASH` | Docker | | Telegram API hash (for local Bot API server) |
| `NODE_ROLE` | HA | `primary` | `primary` (reclaims the lease after `FAILBACK_STABLE_SEC`) or `standby` |
| `NODE_NAME` | HA | hostname | Name shown in `/health`, heartbeats and notifications |
| `PEER_URL` | HA | | Dashboard URL of the *other* node by static LAN IP, e.g. `http://192.168.11.150:8080`. Blank = single node (HA off). Never a hostname (does not resolve inside gluetun) or a floating IP |
| `SYNC_SECRET` | HA | | Shared bearer secret for `/sync/*`; identical on both nodes, separate from `API_TOKEN`. Blank = HA off |
| `HEARTBEAT_INTERVAL_SEC` | HA | `10` | Passive node's heartbeat and feed-pull period |
| `LEASE_TTL_SEC` | HA | `30` | No successful heartbeat for this long = lease lost, the standby takes over. Must be ≥ `HEARTBEAT_INTERVAL_SEC` |
| `FAILBACK_STABLE_SEC` | HA | `180` | Continuous healthy heartbeats the returning primary needs before it asks for the lease back |
| `BACKUP_SKIP_ROLE_CHECK` | Docker | `false` | `true` makes `backup.py` back up even on a passive node (manual replica snapshot) |

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
  handlers/            # Telegram handler modules
  services/            # Background tasks and intent processing
    ha.py              # Role controller (active/standby state machine) and PtbRunner
    sync.py            # Sync client: heartbeat, change-feed pull, snapshot; developer notifier
  dashboard/           # Web dashboard (aiohttp, jinja2, session auth)
    sync.py            # /health and the peer-facing /sync/* endpoints
main.py                # Thin wrapper entry point
info.py                # CLI: video info lookup
subscriptions.py       # CLI: bulk subscription management
empty_media_folder.py  # CLI: clear media folder
backup.py              # CLI: SQLite online backup
preview_dashboard.py   # CLI: start the dashboard with mock data
conftest.py            # Pytest config (silences PTB warnings)
run_tests.sh           # Full test suite runner (unit + integration + E2E)
entrypoint.sh          # Docker entrypoint (cron + bot; backup schedule from BACKUP_CRON)
```

### **Architecture**

**Entry flow:** `main.py` → `dasovbot/__main__.py` → loads config from env vars → initializes yt-dlp → opens SQLite database → starts the dashboard → loads persisted state → builds Telegram Application → registers handlers → starts the role controller, which starts polling and the background tasks only while the node is ACTIVE (a single node is ACTIVE at once; see [High availability](#high-availability)).

**State management:** Central `BotState` dataclass (`state.py`) holds all mutable state: video cache, intents, subscriptions, users, download queue (`asyncio.Queue`). State is accessed via `context.bot_data['state']` in handlers. Changes are persisted immediately (write-through) to a SQLite database (`{CONFIG_FOLDER}/data/bot.db`) via `database.py`; every write stamps a per-node revision (`rev`) and every delete leaves a tombstone, which is what the HA change feed replicates. On first run, existing JSON files are automatically migrated to SQLite.

**Intent system:** Video download requests are modeled as `Intent` objects (not processed immediately). Intents accumulate `chat_ids` and `inline_message_ids` from multiple requesters, with priority based on requester count. A background worker (`intent_processor.py`) processes the queue in priority order — this deduplicates downloads when multiple users request the same video.

**Handler registration:** All handlers registered in `handlers/__init__.py:register_handlers()`. Multi-step flows (download, subscribe, unsubscribe) use `ConversationHandler` with states defined in `constants.py`.

**Background tasks:** Started in `services/background.py:start_background_tasks()` via `asyncio.create_task`:
- Loading-animation population
- Subscription polling (hourly)
- Intent queue processing
- Inline query cache cleanup
- Backup freshness monitoring (alerts the developer if backups stop)
- Media folder sweep (hourly; removes leftover files older than 6 hours)

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

**Error classification:** Video extraction errors are matched against `VIDEO_ERROR_MESSAGES` in `constants.py` to distinguish user-facing errors from internal failures.

### **System dependencies:**
- Python 3.10+
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

- **ACTIVE** polls Telegram and runs every background task (subscriptions, intent worker, inline cache cleanup, media sweep, backup monitoring). **PASSIVE** runs only the dashboard (read-only), `/health` and the sync client. **DRAINING** has stopped polling and is finishing the download and upload in progress.
- The passive node calls the active node's `/sync/heartbeat` every `HEARTBEAT_INTERVAL_SEC` and pulls the change feed after each reply. No successful heartbeat for `LEASE_TTL_SEC` = lease lost → the standby becomes ACTIVE, if it is **ready** (its last sync is within 3 × `LEASE_TTL_SEC` of the last heartbeat it saw; otherwise it stays passive, alerts the developer and keeps trying).
- **Cold start** with no peer in sight: the primary claims after one `HEARTBEAT_INTERVAL_SEC`, the standby after `LEASE_TTL_SEC + HEARTBEAT_INTERVAL_SEC`.
- **Failback:** the returning primary starts PASSIVE, syncs, and after `FAILBACK_STABLE_SEC` of continuous healthy heartbeats requests the lease back (`POST /sync/handoff`). The standby stops polling at once, finishes only the in-progress download and in-flight upload, answers `drained: true`, the primary pulls the final changes and becomes ACTIVE; the standby goes PASSIVE when it sees that. A drained node whose peer never activates within `LEASE_TTL_SEC` re-activates itself: nothing is ever left with nobody polling.
- **Split-brain breakers:** a standby that is ACTIVE and sees the primary ACTIVE in a heartbeat steps down; a standby that gets Telegram's `409 Conflict` steps down at once (the primary logs it and keeps polling). Telegram's 409 only fires between pollers of the *same* Bot API server, so the heartbeat is the real guard: if the LAN between the hosts fails while both are up, both will poll until it heals (accepted).
- **Backlog on activation:** every local Bot API server receives every update and buffers it while nobody polls it, so the first poll after activation returns everything the other node already handled. The activating node drains that backlog first and keeps only messages dated after the peer was last seen ACTIVE (minus one heartbeat interval); inline and callback queries in the backlog are stale and dropped. Duplicates are bounded to about one heartbeat interval; nothing is silently lost.
- **Manual override:** `/system` shows a role card with **Take over** (passive node; readiness still enforced) and **Hand back** (active node; on the primary the button reads *Hand over to standby*). A manual takeover of the standby sets a persisted **hold**: automatic failback is suspended until Hand back is clicked on either node.
- **Notifications:** the developer chat gets every transition (🟢 active, ⏳ handoff requested, 🟡 drained, ⚪ handed over, 🔴 stepped down, ⚠️ cannot take over) and sync errors at most once per 10 minutes.

#### Data sync

The database is replicated by the app, **not** by Syncthing. Every write on the active node stamps the row with a revision from a per-node Lamport-style counter (`sync_meta`); deletes write a `tombstones` row. The passive node pulls `GET /sync/changes?since=<rev>` in pages of 500 ordered by revision and applies rows and tombstones into SQLite and memory through `apply_remote_*` paths that never trigger side effects (no downloads, no messages). Once an hour it pulls `GET /sync/snapshot` (a SQLite backup file) and reconciles against it as a self-healing floor; a fresh standby bootstraps from the snapshot.

When the primary returns after a takeover, the standby wins on every key it touched since the handoff point (its revisions replace the primary's, its tombstones delete), `requests` rows merge by append (unique on user, url, time), and the primary's surviving unseen rows are re-stamped with fresh revisions so they flow back to the standby. Up to `HEARTBEAT_INTERVAL_SEC` of the primary's last writes may be invisible to the standby at takeover; they come back when the primary returns.

**Not synced:** the in-memory inline-query cache (users simply re-query), `animation_file_id` (set `ANIMATION_FILE_ID` on both nodes or each activation re-uploads the animation once), dashboard sessions (log in again on the other node), health alerts, the `/media` working folder. `/export` is replicated by Syncthing; its lag never blocks a takeover.

#### Endpoints

`/health` is public; `/sync/*` require `Authorization: Bearer <SYNC_SECRET>` (never the API token or a session).

| Endpoint | Role | Request | Response |
|---|---|---|---|
| `GET /health` | any | — | `200 {role, node, lease_holder, last_sync_rev, last_sync_at, ready, peer, peer_url, node_role, enabled, rev, manual_hold, handback_requested, drained}`; a node without HA reports `role: active`, `ready: true` |
| `GET /sync/heartbeat` | any | — | `200 {node, role, lease_until, rev, drained, manual_hold, handback_requested, handoff_rev}`; `503` when HA is off |
| `GET /sync/changes` | any | `?since=<rev>&limit=<n≤500>` | `200 {since, until, has_more, rows: {videos, intents, users, subscriptions, banned_users, requests}, tombstones}`; `400` on bad params |
| `GET /sync/snapshot` | any | — | `200` SQLite file (`application/x-sqlite3`, header `X-Dasovbot-Rev`) |
| `POST /sync/handoff` | active | `{"manual": bool, "node": name}` | `202 {accepted, role: draining, drained: false}` on ACTIVE, `200` with the current `drained` when already draining, `409 {error}` when passive or when the primary is asked for an automatic handoff |

Force a takeover from a shell (run against the **active** node, then watch `/health` on the other):

```bash
curl -X POST -H "Authorization: Bearer $SYNC_SECRET" -H "Content-Type: application/json" \
     -d '{"manual": true}' http://192.168.11.150:8080/sync/handoff
curl http://192.168.11.7:8080/health
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

Only the active node's cron runs `backup.py`: it asks `http://127.0.0.1:$DASHBOARD_PORT/health` and exits 0 (skipped) when the node is passive or the bot is not answering, so two cron jobs never prune each other's files in the shared `/data`. `BACKUP_SKIP_ROLE_CHECK=true` (env, or `docker exec dasovbot env BACKUP_SKIP_ROLE_CHECK=true python backup.py`) forces a backup of a passive replica. `monitor_backups` alerts only on the active node.

#### Addressing

Each node keeps its own dashboard address; the passive dashboard shows a banner linking to the active node, and `/health` tells you who is active. There is no floating IP. `PEER_URL` is the peer's **static LAN IP** (`http://192.168.11.150:8080` on the standby, `http://192.168.11.7:8080` on the primary; the Pi has a reserved DHCP lease): both bots run inside gluetun, whose built-in DNS-over-TLS resolver ignores Pi-hole and the router, so LAN hostnames never resolve inside the container. gluetun on both hosts needs `FIREWALL_OUTBOUND_SUBNETS=192.168.11.0/24` so the bot can reach the peer, and each host its own WireGuard key (most providers allow one session per key).

#### Rollout

1. Put the `.stignore` files above on both Syncthing instances.
2. On the standby, delete the stale replica (`bot.db`, `bot.db-wal`, `bot.db-shm` under `/data`); the standby bootstraps from the primary's snapshot.
3. Primary: set `NODE_ROLE=primary`, `NODE_NAME`, `PEER_URL=http://192.168.11.7:<port>`, `SYNC_SECRET`, plus explicit `DASHBOARD_PASSWORD` and `API_TOKEN`; `docker compose up -d` (`vpn → api → bot`); `curl /health` → `role: active`.
4. Standby: the same with `NODE_ROLE=standby`, `NODE_NAME=rpi`, `PEER_URL=http://192.168.11.150:<port>`, the same `SYNC_SECRET`/`DASHBOARD_PASSWORD`/`API_TOKEN`; `docker compose up -d`; watch `/health` go `role: passive`, `ready: true` after the bootstrap snapshot.
5. Rehearse once: Take over on the standby's `/system`, send the bot a link, Hand back, confirm the primary is ACTIVE again and the video shows on both dashboards.

**Rollback:** blank `PEER_URL` on both nodes and restart — each node is standalone again (stop the standby's bot, or both will poll).

**Reading the state:** `curl http://<node>:8080/health` (`role`, `lease_holder`, `ready`, `last_sync_at`); the `/system` card shows the same plus the peer's role, heartbeat age and sync revision; the developer chat messages above mark every transition.
