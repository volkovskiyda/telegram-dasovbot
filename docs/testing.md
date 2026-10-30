# Testing

```bash
pip install -r requirements-test.txt     # pytest, pytest-asyncio, ruff on top of the app requirements
```

## Unit tests

No bot token, no network, about 8 seconds. This is what CI runs (together with `ruff check .`) on Python 3.14 before every image build.

```bash
ruff check .
python -m pytest tests --ignore=tests/integration
python -m pytest tests/test_database.py -v                                   # one file
python -m pytest tests/test_state.py::TestSetVideo -v                        # one class
python -m pytest tests/test_helpers.py::TestRemoveCommandPrefix::test_strips_command -v
```

Running the whole `tests/` directory (`pytest tests`, `unittest discover`) also collects `tests/integration`, which needs a live test bot.

## Integration tests

`tests/integration/` drives the real handlers against a real test bot and the Telegram API: actual conversation flows, inline queries, subscriptions, and (behind flags) real yt-dlp extraction, downloads and uploads through the intent pipeline.

### Setup

1. Create a **separate** test bot with [@BotFather](https://t.me/BotFather) (`/newbot`). Never use the production bot. Enable inline mode (`/setinline`) and set inline feedback to 100 % (`/setinlinefeedback`) if you want the inline and E2E tests.
2. Get your user id from [@userinfobot](https://t.me/userinfobot).
3. `cp .env.test.example .env.test` and fill it in:

| Variable | Required | Description |
|---|---|---|
| `TEST_BOT_TOKEN` | Yes | The test bot's token |
| `TEST_USER_ID` | Yes | Your Telegram user id |
| `TEST_CHAT_ID` | No | Chat for test messages; defaults to `TEST_USER_ID` |
| `TEST_VIDEO_URL` | For `run_tests.sh` and `test_real_pipeline.py` | A real, **short** video URL: it is downloaded and uploaded for real. The handler tests mock extraction and fall back to `https://example.com/video` when it is unset |
| `TEST_BASE_URL` | No | Local Bot API server URL (`http://<host>:8081/bot`); when unreachable, tests fall back to the official API with a warning |
| `TEST_READ_TIMEOUT` | No | Request timeout for the test bot (default 30) |
| `TEST_CHANNEL_URL` | No | Channel for subscription tests (defaults to the channel that owns `TEST_VIDEO_URL`) |
| `TEST_PLAYLIST_URLS` | No | Comma-separated real playlist URLs for subscription tests: each is polled with flat metadata only; with downloads enabled, one test downloads the **shortest** entry (skipped if nothing is under 5 min) |
| `TEST_ENABLE_DOWNLOAD` | No | `1` lets `test_real_pipeline.py` download and upload for real |
| `TEST_LOCAL_MODE` | No | `1` runs the upload tests in PTB local mode (`file://` paths) against a `--local` server |
| `ENABLE_E2E_TESTS` | No | `1` enables the manual end-to-end tests |

`run_tests.sh` sets the last three per stage itself; set them only when running pytest by hand.

### Running

```bash
python -m pytest tests/integration -v                                   # every automated integration test
python -m pytest tests/integration/test_commands.py -v                  # one file
python -m pytest tests/integration/test_commands.py::TestBasicCommands -v
python -m pytest tests/integration -v -s                                # -s shows the progress prints
TEST_ENABLE_DOWNLOAD=1 python -m pytest tests/integration/test_real_pipeline.py -v
```

### Full suite script

```bash
./run_tests.sh           # all stages
./run_tests.sh --no-e2e  # skip the manual E2E stage
```

Four stages in order, stopping on the first failure:

1. **Unit tests**: no credentials or network.
2. **Integration tests** with `TEST_ENABLE_DOWNLOAD=1`: real yt-dlp extraction against YouTube and real Telegram API calls, including an actual download/upload of `TEST_VIDEO_URL`.
3. **LOCAL_MODE uploads** (`TEST_LOCAL_MODE=1`): the intent pipeline hands the video to a local Bot API server as a `file://` path; skipped automatically when `TEST_BASE_URL` is unreachable.
4. **Manual E2E tests** (`ENABLE_E2E_TESTS=1`), always last because they **need you**: the script prints a banner and waits for confirmation, then the test bot messages you in Telegram to send `/start` (30 s window) and to run an inline query and tap the result (120 s window). Skipped when the terminal is non-interactive or with `--no-e2e`.

### Local Bot API server for tests

Stage 3 (and testing against `TEST_BASE_URL` in general) needs a local [telegram-bot-api](https://github.com/volkovskiyda/docker-telegram-bot-api) server started with `--local` that mounts `/tmp/test_config/media` at the same absolute path: that is where the tests write downloaded media, and in local mode the server reads the uploads from that folder by `file://` path. Get `api_id`/`api_hash` at [my.telegram.org](https://my.telegram.org):

```bash
docker run -dit --rm --name telegram-bot-api \
  -e TELEGRAM_API_ID=<api_id> -e TELEGRAM_API_HASH=<api_hash> \
  -v /tmp/test_config/media:/tmp/test_config/media \
  -p 8081:8081 \
  ghcr.io/volkovskiyda/telegram-bot-api --local
```

Then set `TEST_BASE_URL=http://<host>:8081/bot` in `.env.test`.

### What the files cover

- `test_api.py`: Telegram API connectivity with the test bot
- `test_commands.py`: `/start`, `/help`, unknown commands; `TestCommandEndToEnd` is manual
- `test_download.py`: the download conversation flow
- `test_inline.py`: inline query handling; `TestInlineQueryEndToEnd` is manual
- `test_inline_download.py`: inline query → download → send
- `test_subscription.py`: subscription handlers
- `test_real_pipeline.py`: real yt-dlp extraction and, behind `TEST_ENABLE_DOWNLOAD`/`TEST_LOCAL_MODE`, real downloads and uploads through the intent pipeline

Most handler tests mock `extract_info`; `test_real_pipeline.py` deliberately does not.

### Writing one

Every integration test inherits `IntegrationTestBase` (`tests/integration/base.py`), which builds a real bot application with the handlers registered, gives each test a fresh `BotState`, manages startup/shutdown and offers:

```python
await self.simulate_update(update)          # push an Update through the handlers
await self.send_command('/start')           # send a message to your test chat
updates = await self.get_updates()          # pending updates from Telegram
await self.clear_updates()
```

```python
from unittest.mock import AsyncMock, patch
from telegram import Chat, Message, Update, User

from tests.integration.base import IntegrationTestBase


class TestMyFeature(IntegrationTestBase):
    async def test_my_handler(self):
        user = User(id=self.test_config.user_id, first_name='Test', is_bot=False, username='testuser')
        chat = Chat(id=self.test_config.chat_id, type='private')
        message = Message(message_id=1, date=None, chat=chat, from_user=user, text='/mycommand')
        await self.simulate_update(Update(update_id=1, message=message))
        self.assertTrue(some_condition)

    async def test_with_mock(self):
        # extract_info is async: patch it with an AsyncMock whose return value is the VideoInfo-like object
        with patch('dasovbot.handlers.download.extract_info', new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = VideoInfo(title='Test', webpage_url='https://example.com')
            await self.simulate_update(self.make_update('/download https://example.com'))
        mock_extract.assert_awaited_once()
```

Keep tests independent of each other, mock `extract_info` unless the test is about the real pipeline, cover the edge cases (empty input, invalid URL, errors), and print progress in the manual tests so the person at the keyboard knows what to do.

### Troubleshooting

- **"TEST_BOT_TOKEN not set"**: `.env.test` is missing or empty.
- **"Timeout waiting for update"** (E2E): act within the window the bot's message gives you.
- **The bot does not answer**: the test bot is stopped in @BotFather, the token is wrong, or inline mode is off for inline tests.
- **Import errors**: run from the project root (`python -m pytest tests/integration`).

## Further reading

- [python-telegram-bot documentation](https://docs.python-telegram-bot.org/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [pytest documentation](https://docs.pytest.org/)
