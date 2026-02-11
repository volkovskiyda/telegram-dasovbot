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

### **Project structure:**
```
dasovbot/              # Main package
  __main__.py          # Entry point
  config.py            # Config loading, ydl_opts
  constants.py         # Error messages, timeouts, states
  models.py            # Dataclasses for video, intent, subscription
  persistence.py       # JSON file I/O
  state.py             # BotState (mutable state container)
  downloader.py        # yt-dlp wrapper
  helpers.py           # Shared utilities
  handlers/            # Telegram handler modules
  services/            # Background tasks and intent processing
main.py                # Thin wrapper entry point
info.py                # CLI: video info lookup
subscriptions.py       # CLI: bulk subscription management
empty_media_folder.py  # CLI: clear media folder
```

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
docker-compose up -d
```
