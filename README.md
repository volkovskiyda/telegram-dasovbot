# dasovbot

[dasovbot](https://t.me/dasovbot) is a Telegram bot that downloads and shares online video, built on [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) and [yt-dlp](https://github.com/yt-dlp/yt-dlp). It runs as a single container or as a two-node active/standby pair, with a web dashboard, a JSON admin API and a CLI.

- [Using the bot](#using-the-bot)
- [Web dashboard](#web-dashboard)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Architecture](#architecture)
- [Tests](#tests)
- Further docs: [JSON API, CLI and Claude Code skill](docs/api.md) · [High availability](docs/ha.md) · [Testing](docs/testing.md)

## Using the bot

**[Inline mode](https://telegram.org/blog/inline-bots):** `@dasovbot` _video url_ — download and share a video from any chat.

**Commands:**

| Command | Does |
|---|---|
| `/start` | Welcome message |
| `/download` (`/das`, `/dv`) _video url_ | Download a video |
| `/cancel` | Cancel the current operation |
| `/help` | Show the available commands |
| `/subscriptions` (`/subs`) | List your subscriptions |
| `/subscribe` _playlist url_ | Subscribe to a channel feed or playlist |
| `/unsubscribe` _playlist url_ | Unsubscribe |
| `/playlists` | Playlists of the channels you follow |
| `/multiple_subscribe` _playlist urls_ | Bulk subscribe |

## Web dashboard

Password-protected web UI on `DASHBOARD_PORT` (default 8080).

- **Overview** (`/`) — stats cards, processing queue with remove buttons, populate-subscriptions trigger
- **Videos** (`/videos`) — downloaded videos with sorting and source filtering, plus the users who requested each one with ban/unban buttons
- **Ignored** (`/ignored`) — failed/skipped videos with retry and remove actions
- **Subscriptions** (`/subscriptions`) — subscriptions with per-subscriber badges; remove a single subscriber or the whole subscription. A feed yt-dlp cannot find on three hourly polls in a row (channel deleted, renamed, terminated) is marked **dead**: the poll skips it, every subscriber and the developer chat are told once, and the page lists it first with the last error and a **Check** button that polls it again (and revives it when it is back)
- **Users** (`/users`) — users ranked by logged requests, with ban/unban; a user links to the videos they requested. A banned user never gets a new video, but the bot looks like it still works: inline queries go unanswered (the client times out), `/download` shows the loading animation and then `❌ Video unavailable` after 10–60 s, and requests already queued fail the same way on delivery. Subscriptions stay fully usable and keep delivering; only the "show latest videos" shortcut after subscribing is skipped
- **System** (`/system`) — background task status, state sizes, manual subscription polling trigger, and on an HA pair the role card with **Take over** / **Hand back** (see [High availability](docs/ha.md))

Every state-changing dashboard form is a POST that must come from the dashboard's own origin (the `Origin`/`Referer` header is checked, since the session cookie is only `SameSite=Lax`), and the pages refuse to be framed. The machine-readable side of the dashboard, the `/api/` routes and the `dasovbot` CLI, is documented in [docs/api.md](docs/api.md).

## Quick start

### System dependencies

- Python 3.11+ (yt-dlp 2026.10.26 dropped 3.10). The Docker image and CI run **3.15**
- [ffmpeg](https://ffmpeg.org/) — video conversion and yt-dlp post-processing
- [Deno](https://deno.com/) — JavaScript runtime yt-dlp needs for YouTube extraction (`brew install deno`)

### Run locally

```bash
pip install -r requirements.txt
cp .env.example .env     # fill in BOT_TOKEN, DEVELOPER_CHAT_ID, LOADING_VIDEO_ID; see Configuration
python main.py
```

Look a URL up the way the bot sees it (`-d` also downloads it):

```bash
python info.py '<url>'
python info.py '<url>' -d
```

### Docker compose (recommended)

`docker-compose.yml` runs a local [telegram-bot-api](https://github.com/volkovskiyda/docker-telegram-bot-api) server (`api`, started with `--local`) next to the bot. Populate `.env` from `.env.example`, set `BASE_URL=http://api:8081/bot`, then:

```bash
docker compose up -d
docker exec dasovbot python backup.py      # manual database backup (cron does it on BACKUP_CRON)
```

Both containers mount `./config/media/` at `/media`: the bot hands uploads to the server as `file:///media/...` paths, so a multi-GB video never passes through bot memory. `./config/data/` holds the SQLite database and the generated secrets; `./config/export/` is the media library the developer's downloads are moved into. Each exported video gets a slim yt-dlp-style `<name>.info.json` sidecar next to it (`VideoInfo.to_sidecar()`: id, title, channel, duration, upload date, tags, categories, description, thumbnail, chapters), so a library index can be built from the folder without fetching the metadata again.

### Plain docker

```bash
docker run -dit --rm --name telegram --pull=always -e TELEGRAM_API_ID=<api_id> -e TELEGRAM_API_HASH=<api_hash> -v $PWD/config/media:/media -p 8081:8081 ghcr.io/volkovskiyda/telegram-bot-api --local
docker run -dit --rm --name dasovbot --pull=always -e READ_TIMEOUT=30 -e BASE_URL=http://host.docker.internal:8081/bot -e LOCAL_MODE=true -e CONFIG_FOLDER=/ -e BOT_TOKEN=<your_bot_token> -e LOADING_VIDEO_ID=<loading_animation_video_url> -e DEVELOPER_CHAT_ID=<developer_chat_id> -v $PWD/config/data:/data -v $PWD/config/media:/media ghcr.io/volkovskiyda/dasovbot
```

Replace `<api_id>`, `<api_hash>`, `<your_bot_token>`, `<loading_animation_video_url>` and `<developer_chat_id>`. The `/data` mount keeps the database outside the (`--rm`) container; without it the database is lost when the container is removed.

Images are published to `ghcr.io/volkovskiyda/dasovbot` and Docker Hub on every push to `main` and on every yt-dlp release (tag `YYYY.MM.DD`, `.1`/`.2` on same-day collisions; `latest` follows).

## Configuration

Everything comes from environment variables (`.env` via python-dotenv locally, the `environment:` block in `docker-compose.yml`). Get `BOT_TOKEN` from @BotFather ([tutorial](https://core.telegram.org/bots/tutorial#obtain-your-bot-token)) and turn inline mode on with a placeholder and 100 % inline feedback (`/setinline`, `/setinlinefeedback`). `BASE_URL` is either `https://api.telegram.org/bot` or a local [telegram-bot-api](https://github.com/tdlib/telegram-bot-api) server; when that server runs with `--local`, also set `LOCAL_MODE=true` and mount the media folder at the same absolute path in both containers. `DEVELOPER_CHAT_ID` receives the uploads and every alert; `LOADING_VIDEO_ID` is the clip shown as the loading animation.

**Generated secrets.** `DASHBOARD_PASSWORD`, `API_TOKEN` and `ADMIN_TOKEN` may be left blank: the bot generates a strong value once, writes it to `data/dashboard_password.txt`, `data/api_token.txt` or `data/admin_token.txt` (mode 0600) and reads it back on every later start, so it stays stable across restarts. The files are per host; on an HA pair set all three explicitly to the same value on both nodes and keep the files out of Syncthing (see [docs/ha.md](docs/ha.md#syncthing)).

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | Yes | | Telegram bot token from @BotFather |
| `BASE_URL` | Yes | | Telegram Bot API base URL |
| `DEVELOPER_CHAT_ID` | Yes | | Chat ID for developer notifications and uploads |
| `DEVELOPER_ID` | No | `DEVELOPER_CHAT_ID` | Developer user ID for export permissions |
| `READ_TIMEOUT` | No | `30` | Bot API request timeout in seconds. `.env.example` uses 300: a local server with `--local` receives multi-GB uploads in one request |
| `LOCAL_MODE` | No | `false` | Pass uploads to the Bot API server as `file://` paths instead of multipart bodies. Requires a server started with `--local` that sees the media folder at the same path |
| `BASE_FILE_URL` | No | derived from `BASE_URL` | Bot API file-download base URL (`.../file/bot`) |
| `UPLOAD_CONCURRENCY` | No | `1` | Max simultaneous video-file uploads; keep at `1` to bound memory and IO |
| `LOADING_VIDEO_ID` | No | | Video URL used for the loading animation |
| `ANIMATION_FILE_ID` | No | | Pre-cached animation file ID (skips the loading upload) |
| `CONFIG_FOLDER` | No | `./config` | Root folder for the data/media/export directories. Docker deployments must set `/` so data lands on the mounted `/data`, `/media`, `/export` volumes (docker-compose.yml does) |
| `EMPTY_MEDIA_FOLDER` | No | `false` | Clear the media folder when the intent worker crashes and restarts. `.env.example` sets `true`: in a container nothing else lives there |
| `DISK_FREE_MIN_GB` | No | `20` | Free space below which the media, export or data volume raises a health alert (checked hourly); `0` disables |
| `DASHBOARD_PASSWORD` | No | generated | Dashboard password; see *Generated secrets* above. Same value on both nodes of an HA pair |
| `DASHBOARD_PORT` | No | `8080` | Dashboard, API and sync port |
| `DASHBOARD_BEHIND_PROXY` | No | `false` | Set `true` when the dashboard sits behind a reverse proxy (Traefik, nginx, …): login rate limiting uses the client IP from `X-Forwarded-For` (plus a 5× larger budget per socket peer, so a client that reaches the port without the proxy cannot rotate the header past the limit), the session cookie is marked `Secure` when the proxy reports HTTPS via `X-Forwarded-Proto`, and `X-Forwarded-Host` counts as the dashboard's own origin |
| `API_TOKEN` | No | generated | Read-only bearer token for `/api/videos`; see *Generated secrets*. Same value on both HA nodes |
| `ADMIN_TOKEN` | No | generated | Bearer token for the admin API, accepted on every `/api/` route; see *Generated secrets*. Same value on both HA nodes; `API_TOKEN` cannot call admin routes |
| `COOKIES_FILE` | No | | Path to a cookies file for yt-dlp |
| `BACKUP_CRON` | Docker | | Cron schedule for automatic SQLite backups (`entrypoint.sh` installs it into cron; empty disables). docker-compose defaults it to `0 */12 * * *` |
| `BACKUP_MAX_COUNT` | Docker | `14` | Backups kept by `backup.py`; older ones are pruned |
| `DB_PATH` | Docker | `/data/bot.db` | Database path `backup.py` reads from |
| `BACKUP_DIR` | Docker | folder of `DB_PATH` | Folder `backup.py` writes backups to |
| `BACKUP_SKIP_ROLE_CHECK` | Docker | `false` | `true` makes `backup.py` back up even on a passive node (manual replica snapshot) |
| `TELEGRAM_API_ID` | Docker | | Telegram API ID (for the local Bot API server) |
| `TELEGRAM_API_HASH` | Docker | | Telegram API hash (for the local Bot API server) |
| `NODE_ROLE` | HA | `primary` | `primary` (reclaims the lease after `FAILBACK_STABLE_SEC`) or `standby` |
| `NODE_NAME` | HA | hostname | Name shown in `/health`, heartbeats and notifications |
| `PEER_URL` | HA | | Dashboard URL of the *other* node by static LAN IP, e.g. `http://192.168.1.10:8080`. Blank = single node (HA off). Never a hostname (does not resolve inside gluetun) or a floating IP |
| `SYNC_SECRET` | HA | | Shared bearer secret for `/sync/*`; identical on both nodes, separate from `API_TOKEN`. Blank = HA off |
| `HEARTBEAT_INTERVAL_SEC` | HA | `10` | Passive node's heartbeat and feed-pull period |
| `LEASE_TTL_SEC` | HA | `30` | No active peer for this long = lease lost, the standby takes over. Also the cold-start wait of the primary. Must be ≥ `HEARTBEAT_INTERVAL_SEC` |
| `FAILBACK_STABLE_SEC` | HA | `180` | Continuous healthy heartbeats the returning primary needs before it asks for the lease back |
| `VPN_CONTROL_APIKEY` | No | | Key of a gluetun control-server auth role that may `GET /v1/publicip/ip` and `GET /v1/vpn/status`; blank = no VPN poller and `vpn: null` in `/api/status` |
| `VPN_CONTROL_URL` | No | `http://127.0.0.1:8000` | gluetun control server, reachable from the bot (shared network namespace when the bot runs inside the VPN container) |

## Project structure

```
dasovbot/              # Main package
  __main__.py          # Entry point
  config.py            # Config loading, ydl_opts
  constants.py         # Error messages, timeouts, states
  models.py            # Dataclasses for video, intent, subscription
  database.py          # SQLite persistence (aiosqlite), change feed, snapshot reconcile
  persistence.py       # File utilities (remove, media sweeps)
  state.py             # BotState (mutable state container, write-through DB)
  downloader.py        # yt-dlp wrapper, cancellable downloads, ffmpeg conversion
  helpers.py           # Shared utilities
  cli.py               # `dasovbot` CLI for the admin API (stdlib only, installed via pyproject.toml)
  handlers/            # Telegram handler modules (download, inline, subscription, ban, common)
  services/
    background.py      # Hourly tasks: subscriptions, inline cache, media sweep, backups, disk, tombstones
    intent_processor.py# Download execution and Telegram delivery
    ban.py             # Banned-user behaviour (fake failures)
    ha.py              # Role controller (active/standby state machine) and PtbRunner
    sync.py            # Sync client: heartbeat, change-feed pull, snapshot; developer notifier
    vpn.py             # VPN status poller against the gluetun control server
  dashboard/           # Web dashboard (aiohttp, jinja2, session auth)
    server.py          # App factory, middlewares, routes, generated secrets
    auth.py            # Sessions, bearer tokens, login rate limit, cross-site check
    views.py           # HTML pages and their POST actions
    api.py             # Read-only JSON API (/api/videos, API_TOKEN)
    admin_api.py       # Admin JSON API (status, users, subscriptions, intents, alerts, playlists, writes; ADMIN_TOKEN)
    openapi.py         # OpenAPI 3.1 description served at /api/openapi.json
    sync.py            # /health and the peer-facing /sync/* endpoints
docs/                  # api.md, ha.md, testing.md
skills/dasovbot/       # Claude Code skill: CLI reference (generated from --help) + operator playbooks
main.py                # Thin wrapper entry point
pyproject.toml         # Packaging for the CLI only (`pipx install -e .`) and the ruff config; the bot installs requirements.txt
requirements.txt       # Bot dependencies, exact except yt-dlp; requirements-test.txt adds pytest, pytest-asyncio, ruff
constraints.txt        # Exact transitive versions (applied by requirements.txt); yt-dlp's own dependencies float on purpose
info.py                # CLI: video info lookup
subscriptions.py       # CLI: bulk subscription import straight into bot.db (superseded by `dasovbot subscribe`)
empty_media_folder.py  # CLI: clear media folder
backup.py              # CLI: SQLite online backup
preview_dashboard.py   # CLI: start the dashboard with mock data
conftest.py            # Pytest config (silences PTB warnings)
run_tests.sh           # Full test suite runner (unit + integration + E2E)
entrypoint.sh          # Docker entrypoint (cron + bot; backup schedule from BACKUP_CRON)
```

## Architecture

**Entry flow:** `main.py` → `dasovbot/__main__.py` → loads config from env vars → initializes yt-dlp → opens the SQLite database → starts the dashboard → loads persisted state → builds the Telegram Application → registers handlers → starts the role controller, which starts polling and the background tasks only while the node is ACTIVE (a single node is ACTIVE at once; see [High availability](docs/ha.md)). A failed start exits non-zero.

**State management:** the central `BotState` dataclass (`state.py`) holds all mutable state: video cache, intents, subscriptions, users, download queue (`asyncio.Queue`). Handlers reach it through `context.bot_data['state']`. Changes are persisted immediately (write-through) to SQLite (`{CONFIG_FOLDER}/data/bot.db`, WAL, `synchronous=NORMAL`) via `database.py`; every write stamps a per-node revision (`rev`) and every delete leaves a tombstone, which is what the HA change feed replicates. On first run, existing JSON files are migrated to SQLite.

**Intent system:** download requests are modeled as `Intent` objects, not processed immediately. Intents accumulate `chat_ids` and `inline_message_ids` from multiple requesters, with priority based on requester count. A background worker (`intent_processor.py`) processes the queue in priority order, which deduplicates downloads when several users request the same video. *Any new download path must go through the intent system.*

**Handler registration:** all handlers are registered in `handlers/__init__.py:register_handlers()`. Updates are processed concurrently (`MAX_CONCURRENT_UPDATES`): the download and inline handlers await a yt-dlp metadata extraction, and PTB's default serial queue let one slow URL stall every other user. Multi-step flows (download, subscribe, unsubscribe) use `ConversationHandler` with states defined in `constants.py`.

**Background tasks** (`services/background.py:start_background_tasks()`, ACTIVE node only):
- Loading-animation population (once)
- Subscription polling (hourly)
- Intent queue processing
- Inline query cache cleanup (10 min)
- Backup freshness monitoring (hourly; alerts the developer if backups stop)
- Media folder sweep (hourly; removes leftover files older than 6 hours)
- Disk space monitoring (hourly; a dashboard alert and one developer message per episode when the media, export or data volume has less than `DISK_FREE_MIN_GB` free)
- Tombstone pruning (hourly; change-feed tombstones older than 30 days)

The web dashboard is started separately in `__main__.py` (`start_dashboard`) before the Telegram application is built, and keeps running in every role.

**Video processing pipeline:**
1. User sends a URL → the handler creates an `Intent`
2. The worker `monitor_process_intents` wakes on the `asyncio.Queue` and picks the highest-priority eligible intent
3. `intent_processor.py` extracts metadata and downloads via yt-dlp (blocking calls run in an executor). Each download is a `DownloadAttempt`: on timeout it is cancelled at yt-dlp's next progress callback, and a failed or cancelled attempt deletes everything it wrote (`.part`, fragments, merged output)
4. Non-MP4 videos (MKV, WebM, …) are converted to MP4 via ffmpeg: fast remux first, transcode fallback
5. The video is posted to Telegram and its `file_id` cached for reuse. With `LOCAL_MODE=true` the bot sends only the file path (`file:///media/...`) and the Bot API server reads the bytes from the shared media volume. Sends that upload an actual file hold `state.upload_semaphore` (`UPLOAD_CONCURRENCY`, default 1), so the intent worker, the retry fallback and background tasks never upload concurrently

**Models:** all domain objects (`models.py`) are dataclasses with manual `to_dict()`/`from_dict()` serialization (stored as JSON within SQLite); no ORM.

**Video caching:** `VideoInfo` objects are cached in `state.videos` under two keys, the user's query and the canonical URL (the videos API deduplicates by YouTube id). Once a video has a Telegram `file_id`, it is served instantly without re-downloading.

**Error classification:** extraction errors are matched against `VIDEO_ERROR_MESSAGES` in `constants.py` to tell user-facing errors (dead video → the intent is *ignored*) from internal failures. Errors matching `TRANSIENT_ERROR_MESSAGES` (YouTube's bot check, an expired or throttled media URL, 429/5xx) are waited out: the intent backs off for `INTENT_TRANSIENT_BACKOFF_SEC` (15 min) without spending one of its `MAX_INTENT_RETRIES`, up to `MAX_TRANSIENT_RETRIES` times in a row, before it counts as a real failure.

## Tests

```bash
pip install -r requirements-test.txt
ruff check .                                        # what CI runs first
python -m pytest tests --ignore=tests/integration   # unit tests: no token, no network, ~8 s
./run_tests.sh                                      # unit + integration + LOCAL_MODE + manual E2E (needs .env.test)
```

CI (`.github/workflows/docker.yml`) runs ruff and the unit suite on Python 3.15 before every image build. Integration tests, the test bot setup and the E2E stage are described in [docs/testing.md](docs/testing.md).
