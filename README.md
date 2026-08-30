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
- **Videos** (`/videos`) — downloaded videos with sorting and source filtering
- **Ignored** (`/ignored`) — failed/skipped videos with retry and remove actions
- **Subscriptions** (`/subscriptions`) — subscriptions with per-subscriber badges, remove a single subscriber or the whole subscription
- **System** (`/system`) — background task status, state sizes, manual subscription polling trigger

### **JSON API**
Machine-readable video metadata served on the same port under `/api/`, authorized per-request with `Authorization: Bearer <API_TOKEN>` (never the dashboard session cookie). Entry key names mirror yt-dlp's `.info.json` (`id`, `title`, `channel`, `channel_id`, `duration`, `upload_date`, `tags`, `categories`, `description`, `thumbnail`, `chapters`, `epoch`), so consumers of a sidecar-built library index can parse them unchanged; `webpage_url` and `exported` (file moved to the export folder / media library) are added.

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
| `DASHBOARD_PASSWORD` | No | | Password for web dashboard access (auto-generated if not set; written to `data/dashboard_password.txt`) |
| `DASHBOARD_PORT` | No | `8080` | Port for web dashboard server |
| `DASHBOARD_BEHIND_PROXY` | No | `false` | Set `true` when the dashboard sits behind a reverse proxy (Traefik, nginx, …): login rate limiting uses the client IP from `X-Forwarded-For`, and the session cookie is marked `Secure` when the proxy reports HTTPS via `X-Forwarded-Proto` |
| `API_TOKEN` | No | | Bearer token for the JSON API under `/api/` (auto-generated if not set; written to `data/api_token.txt`) |
| `COOKIES_FILE` | No | | Path to cookies file for yt-dlp |
| `BACKUP_CRON` | Docker | | Cron schedule for automatic SQLite backups (`entrypoint.sh` installs it into cron; empty disables). docker-compose defaults it to `0 */12 * * *` |
| `BACKUP_MAX_COUNT` | Docker | `14` | Backups kept by `backup.py`; older ones are pruned |
| `DB_PATH` | Docker | `/data/bot.db` | Database path `backup.py` reads from |
| `BACKUP_DIR` | Docker | folder of `DB_PATH` | Folder `backup.py` writes backups to |
| `TELEGRAM_API_ID` | Docker | | Telegram API ID (for local Bot API server) |
| `TELEGRAM_API_HASH` | Docker | | Telegram API hash (for local Bot API server) |

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
  dashboard/           # Web dashboard (aiohttp, jinja2, session auth)
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

**Entry flow:** `main.py` → `dasovbot/__main__.py` → loads config from env vars → initializes yt-dlp → opens SQLite database and loads persisted state → builds Telegram Application → registers handlers → starts background tasks → runs polling loop.

**State management:** Central `BotState` dataclass (`state.py`) holds all mutable state: video cache, intents, subscriptions, users, download queue (`asyncio.Queue`). State is accessed via `context.bot_data['state']` in handlers. Changes are persisted immediately (write-through) to a SQLite database (`{CONFIG_FOLDER}/data/bot.db`) via `database.py`. On first run, existing JSON files are automatically migrated to SQLite.

**Intent system:** Video download requests are modeled as `Intent` objects (not processed immediately). Intents accumulate `chat_ids` and `inline_message_ids` from multiple requesters, with priority based on requester count. A background worker (`intent_processor.py`) processes the queue in priority order — this deduplicates downloads when multiple users request the same video.

**Handler registration:** All handlers registered in `handlers/__init__.py:register_handlers()`. Multi-step flows (download, subscribe, unsubscribe) use `ConversationHandler` with states defined in `constants.py`.

**Background tasks:** Started in `services/background.py:start_background_tasks()` via `asyncio.create_task`:
- Loading-animation population
- Subscription polling (hourly)
- Intent queue processing
- Inline query cache cleanup
- Backup freshness monitoring (alerts the developer if backups stop)

The web dashboard is started separately in `__main__.py` (`start_dashboard`) before the Telegram application is built.

**Video processing pipeline:**
1. User sends URL → handler creates an `Intent` (download request)
2. Background task `monitor_process_intents` picks up intents from an `asyncio.Queue`
3. `intent_processor.py` extracts metadata and downloads via yt-dlp (blocking calls run in executor)
4. Non-MP4 videos (MKV, WebM, etc.) are converted to MP4 via ffmpeg — fast remux first, transcode fallback
5. Video posted to Telegram, `file_id` cached for future reuse. With `LOCAL_MODE=true` the bot sends only the file path (`file:///media/...`) and the Bot API server reads the bytes from the shared media volume — the video never passes through bot memory. Sends that upload an actual file hold `state.upload_semaphore` (`UPLOAD_CONCURRENCY`, default 1), so the intent worker, the retry fallback, and background tasks never upload concurrently

**Models:** All domain objects (`models.py`) are dataclasses with manual `to_dict()`/`from_dict()` serialization (stored as JSON within SQLite) — no ORM or external serialization library.

**Key modules:**
- `handlers/` — Telegram command and inline query handlers (`download.py`, `inline.py`, `subscription.py`, `common.py`)
- `services/background.py` — Hourly subscription polling, intent queue processing, inline cache cleanup
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
