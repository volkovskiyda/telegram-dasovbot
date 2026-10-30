# Integration tests

Real handlers against a real test bot and the Telegram API. Setup (test bot, `.env.test`, the local Bot API server), the `run_tests.sh` stages, what each file covers and how to write a new test are all in [docs/testing.md](../../docs/testing.md).

```bash
cp .env.test.example .env.test            # fill in TEST_BOT_TOKEN, TEST_USER_ID, TEST_VIDEO_URL
python -m pytest tests/integration -v     # automated tests
./run_tests.sh                            # unit + integration + LOCAL_MODE + manual E2E
```
