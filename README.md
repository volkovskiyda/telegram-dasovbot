## Telegram Bot

### [dasovbot](https://t.me/dasovbot) is a telegram bot to download and share online video.

#### Based on [python telegram bot](https://github.com/python-telegram-bot/python-telegram-bot) and [yt-dlp](https://github.com/yt-dlp/yt-dlp)

### **[Inline mode:](https://telegram.org/blog/inline-bots)**
`@dasovbot` _video url_ - Download and share video

### **Available commands:**
`/download` _video url_ - Download video

`/help` - Show available commands

#### **Subscriptions:**
`/subscriptions` - Show list of subscriptions

`/subscribe` _playlist url_ - Subscribe to playlist

`/unsubscribe` _playlist url_ - Unsubscribe from playlist

### **Configuration:**
- Copy `.env.example` file to `.env` and change `READ_TIMEOUT`, `BASE_URL`, `BOT_TOKEN`, `DEVELOPER_CHAT_ID` and `LOADING_VIDEO_ID` environment variables.
- `READ_TIMEOUT` variable sets the waiting timeout for bot requests
- `BASE_URL` and `BOT_TOKEN` environment variables used to initialize bot.
- For `BASE_URL` you can use standard `https://api.telegram.org/bot` or use a local server ([tutorial](https://github.com/tdlib/telegram-bot-api)).
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
| `LOADING_VIDEO_ID` | No | | Video URL used for loading animation |
| `ANIMATION_FILE_ID` | No | | Pre-cached animation file ID (skips loading upload) |
| `CONFIG_FOLDER` | No | `/` | Root folder for data/media/export directories |
| `EMPTY_MEDIA_FOLDER` | No | `false` | Clear media folder on process crash recovery |
| `DASHBOARD_PASSWORD` | No | | Password for web dashboard access (auto-generated if not set) |
| `DASHBOARD_PORT` | No | `8080` | Port for web dashboard server |
| `COOKIES_FILE` | No | | Path to cookies file for yt-dlp |
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
entrypoint.sh          # Docker entrypoint (cron + bot)
backup-cron            # Cron schedule for database backups
```

### **Architecture**

**Entry flow:** `main.py` → `dasovbot/__main__.py` → loads config from env vars → initializes yt-dlp → opens SQLite database and loads persisted state → builds Telegram Application → registers handlers → starts background tasks → runs polling loop.

**State management:** Central `BotState` dataclass (`state.py`) holds all mutable state: video cache, intents, subscriptions, users, download queue (`asyncio.Queue`). State is accessed via `context.bot_data['state']` in handlers. Changes are persisted immediately (write-through) to a SQLite database (`{CONFIG_FOLDER}/data/bot.db`) via `database.py`. On first run, existing JSON files are automatically migrated to SQLite.

**Intent system:** Video download requests are modeled as `Intent` objects (not processed immediately). Intents accumulate `chat_ids` and `inline_message_ids` from multiple requesters, with priority based on requester count. A background worker (`intent_processor.py`) processes the queue in priority order — this deduplicates downloads when multiple users request the same video.

**Handler registration:** All handlers registered in `handlers/__init__.py:register_handlers()`. Multi-step flows (download, subscribe, unsubscribe) use `ConversationHandler` with states defined in `constants.py`.

**Background tasks:** Started in `services/background.py:start_background_tasks()` via `asyncio.gather`:
- Subscription polling (hourly)
- Intent queue processing
- Inline query cache cleanup
- Web dashboard server

**Video processing pipeline:**
1. User sends URL → handler creates an `Intent` (download request)
2. Background task `monitor_process_intents` picks up intents from an `asyncio.Queue`
3. `intent_processor.py` extracts metadata and downloads via yt-dlp (blocking calls run in executor)
4. Video posted to Telegram, `file_id` cached for future reuse

**Models:** All domain objects (`models.py`) are dataclasses with manual `to_dict()`/`from_dict()` serialization (stored as JSON within SQLite) — no ORM or external serialization library.

**Key modules:**
- `handlers/` — Telegram command and inline query handlers (`download.py`, `inline.py`, `subscription.py`, `common.py`)
- `services/background.py` — Hourly subscription polling, intent queue processing, inline cache cleanup
- `services/intent_processor.py` — Download execution and Telegram posting
- `downloader.py` — yt-dlp wrapper with `asyncio.Lock` for synchronized access
- `dashboard/` — aiohttp web server with cookie-based session auth, jinja2 templates, overview/videos/system pages

**Subscriptions:** Playlist URLs mapped to subscriber chat IDs. Background task polls hourly, creates intents for new videos.

**Video caching:** `VideoInfo` objects cached by URL in `state.videos`. Once a video has a Telegram `file_id`, it's served instantly without re-downloading.

**Error classification:** Video extraction errors are matched against `VIDEO_ERROR_MESSAGES` in `constants.py` to distinguish user-facing errors from internal failures.

### **Run:**
Note: Use Python 3.10 or above to install and run the Bot.
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
python info.py '<url>' -d=False
```
You can pass parameter `-d` (`--download`) to dowload video
```bash
python info.py '<url>' --download=True
```

### **Tests:**
```bash
python -m unittest discover -s tests -v
```

### **Integration tests:**
Requires `.env.test` with test bot credentials. See `tests/integration/README.md` for setup.
```bash
python -m unittest discover -s tests/integration -v
```

### **Docker container**

```bash
docker run -dit --rm --name telegram --pull=always -e TELEGRAM_API_ID=<api_id> -e TELEGRAM_API_HASH=<api_hash> -p 8081:8081 ghcr.io/volkovskiyda/telegram-bot-api ; docker run -dit --rm --name dasovbot --pull=always -e READ_TIMEOUT=30 -e BASE_URL=http://host.docker.internal:8081/bot -e BOT_TOKEN=<your_bot_token> -e LOADING_VIDEO_ID=<loading_animation_video_url> -e DEVELOPER_CHAT_ID=<developer_chat_id> ghcr.io/volkovskiyda/dasovbot
```
##### **Note**: change `<api_id>`, `<api_hash>`, `<your_bot_token>`, `<loading_animation_video_url>` and `<developer_chat_id>`

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
