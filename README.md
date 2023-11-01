## Telegram Bot

### [dasovbot](https://t.me/dasovbot) is a telegram bot to download and share online video.

#### Based on [python telegram bot](https://github.com/python-telegram-bot/python-telegram-bot) and [yt-dlp](https://github.com/yt-dlp/yt-dlp)

### **Available commands:**
`/das` - download specified video url

### **[Inline mode:](https://telegram.org/blog/inline-bots)**
`@dasovbot <video url>` in chat or group - download video and send it

### **Configuration:**
- Copy `.env.example` file to `.env` and change `BASE_URL`, `BOT_TOKEN`, `DEVELOPER_CHAT_ID` and `LOADING_VIDEO_ID` environment variables.
- `BASE_URL` and `BOT_TOKEN` environment variables used to initialize bot.
- For `BASE_URL` you can use standard `https://api.telegram.org/bot` or use a local server ([tutorial](https://github.com/tdlib/telegram-bot-api)).
- Obtain `BOT_TOKEN` via @BotFather ([tutorial](https://core.telegram.org/bots/tutorial#obtain-your-bot-token))
- More info at [official github repository](https://github.com/tdlib/telegram-bot-api)
- `DEVELOPER_CHAT_ID` and `LOADING_VIDEO_ID` environment variables are used to populate loading animation
- For local server you can use [docker telegram bot api image](https://github.com/volkovskiyda/docker-telegram-bot-api)

### **Run:**
Note: Use Python 3.6 or above to install and run the Bot, previous version are unsupported.
- Install requirements
```bash
pip3 install -r requirements.txt
```
- Run the bot
```bash
python3 main.py
```

### **Docker container**

```bash
docker run -dit --rm --name telegram --pull=always -e TELEGRAM_API_ID=<api_id> -e TELEGRAM_API_HASH=<api_hash> -p 8081:8081 ghcr.io/volkovskiyda/telegram-bot-api ; docker run -dit --rm --name dasovbot --pull=always -e BASE_URL=http://host.docker.internal:8081/bot -e BOT_TOKEN=<your_bot_token> -e LOADING_VIDEO_ID=<loading_animation_video_url> -e DEVELOPER_CHAT_ID=<developer_chat_id> ghcr.io/volkovskiyda/dasovbot
```
##### **Note**: change `<api_id>`, `<api_hash>`, `<your_bot_token>`, `<loading_animation_video_url>` and `<developer_chat_id>`

### **Docker compose**
##### **Note**: Populate `.env` based on `.env.example`. See [Configuration](#configuration) for details
### Normal mode (without vpn)
#### Change `BASE_URL` in `.env`:
`BASE_URL=http://host.docker.internal:8081/bot`
```bash
docker-compose up -d
```
### Using vpn
#### Put *.ovpn files into `vpn` folder
#### Change `BASE_URL` in `.env`:
`BASE_URL=http://localhost:8081/bot`
```bash
docker-compose -f docker-compose-vpn.yml up -d
```
