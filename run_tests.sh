#!/usr/bin/env bash
# Full test suite runner: unit -> integration (with real downloads) ->
# LOCAL_MODE uploads -> manual E2E tests (interactive, last).
#
# Requires .env.test (copy .env.test.example) with at least:
#   TEST_BOT_TOKEN, TEST_USER_ID, TEST_CHAT_ID, TEST_VIDEO_URL (short clip!)
# Optional: TEST_BASE_URL (local Bot API server), TEST_CHANNEL_URL
#
# Usage: ./run_tests.sh [--no-e2e]

set -u
cd "$(dirname "$0")"

DOCKER_CMD='docker run -dit --rm --name telegram-bot-api \
  -e TELEGRAM_API_ID=<api_id> -e TELEGRAM_API_HASH=<api_hash> \
  -v /tmp/test_config/media:/tmp/test_config/media \
  -p 8081:8081 \
  ghcr.io/volkovskiyda/telegram-bot-api --local'

run_e2e=1
[[ "${1:-}" == "--no-e2e" ]] && run_e2e=0

banner() { echo; echo "=== $1 ==="; }
fail() { echo; echo "FAILED at stage: $1"; exit 1; }

if [[ ! -f .env.test ]]; then
    echo "Missing .env.test — copy .env.test.example and fill in your test bot credentials:"
    echo "  cp .env.test.example .env.test"
    exit 1
fi

# ---- Local Bot API server probe -------------------------------------------
base_url=$(grep -E '^TEST_BASE_URL=' .env.test | tail -1 | cut -d= -f2-)
server_reachable=0
if [[ -n "$base_url" ]]; then
    if curl -s -m 3 -o /dev/null "${base_url%/bot}"; then
        server_reachable=1
        echo "Local Bot API server reachable at $base_url"
    else
        echo "WARNING: local Bot API server at $base_url is unreachable."
        echo "Integration tests will fall back to the official Telegram API,"
        echo "and the LOCAL_MODE stage will be skipped."
        echo
        echo "To run it (get api_id/api_hash from https://my.telegram.org):"
        echo "$DOCKER_CMD"
    fi
else
    echo "TEST_BASE_URL not set — using the official Telegram API."
    echo "To test against a local Bot API server, start one and set TEST_BASE_URL:"
    echo "$DOCKER_CMD"
fi

# ---- Stage 1: unit tests (no credentials, no network) ---------------------
banner "Stage 1/4: unit tests"
python -m pytest tests --ignore=tests/integration -q || fail "unit tests"

# ---- Stage 2: integration tests, real downloads included ------------------
banner "Stage 2/4: integration tests (real yt-dlp extraction + downloads)"
ENABLE_E2E_TESTS= TEST_ENABLE_DOWNLOAD=1 \
    python -m pytest tests/integration -q || fail "integration tests"

# ---- Stage 3: LOCAL_MODE uploads through the local Bot API server ---------
banner "Stage 3/4: LOCAL_MODE upload pipeline"
if [[ "$server_reachable" == 1 ]]; then
    echo "Note: the server must run with --local AND mount /tmp/test_config/media"
    echo "at the same path (see the docker command above), or this stage fails."
    ENABLE_E2E_TESTS= TEST_ENABLE_DOWNLOAD=1 TEST_LOCAL_MODE=1 \
        python -m pytest tests/integration/test_real_pipeline.py::TestLocalModeIntentPipeline -q \
        || fail "LOCAL_MODE tests"
else
    echo "Skipped: local Bot API server unreachable."
fi

# ---- Stage 4: manual E2E tests (interactive, always last) -----------------
banner "Stage 4/4: manual E2E tests"
if [[ "$run_e2e" == 0 ]]; then
    echo "Skipped (--no-e2e)."
elif [[ ! -t 0 ]]; then
    echo "Skipped: not an interactive terminal. Run ./run_tests.sh from a"
    echo "terminal (or the E2E pytest command from the README) to include them."
else
    echo "*** USER INTERACTION REQUIRED ***"
    echo "Open your Telegram chat with the test bot NOW. The bot will message"
    echo "you the steps:"
    echo "  1. Send /start when asked            (30 second window)"
    echo "  2. Paste the inline query it shows you, wait for the result,"
    echo "     then tap it                       (120 second window)"
    echo "Requires inline mode and 100% inline feedback via @BotFather."
    echo
    read -r -p "Ready? [Y/n] " answer
    if [[ "${answer:-Y}" =~ ^[Nn] ]]; then
        echo "Skipped by user."
    else
        ENABLE_E2E_TESTS=1 TEST_ENABLE_DOWNLOAD=1 \
            python -m pytest \
            tests/integration/test_commands.py::TestCommandEndToEnd \
            tests/integration/test_inline.py::TestInlineQueryEndToEnd \
            tests/integration/test_real_pipeline.py::TestInlineRealUploadE2E \
            -v -s || fail "E2E tests"
    fi
fi

echo
echo "All stages passed."
