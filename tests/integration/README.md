# Integration Tests

This directory contains integration tests for the Telegram bot that use a real test bot and Telegram API.

## Overview

Unlike unit tests that mock Telegram components, these integration tests:
- Use a real Telegram bot token (test bot)
- Test actual handler logic and conversation flows
- Verify the bot's behavior in a near-production environment
- Can optionally test end-to-end flows with manual interaction

## Setup

### 1. Create a Test Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Use `/newbot` to create a new test bot
3. Save the bot token you receive
4. (Optional) Enable inline mode with `/setinline` if testing inline queries

### 2. Get Your User ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. Note your user ID

### 3. Configure Test Environment

1. Copy the example config:
   ```bash
   cp .env.test.example .env.test
   ```

2. Edit `.env.test` and fill in:
   ```bash
   TEST_BOT_TOKEN=your_test_bot_token_here
   TEST_USER_ID=your_telegram_user_id
   TEST_CHAT_ID=your_telegram_user_id  # Can be same as USER_ID for private chats
   ```

### 4. Install Test Dependencies

```bash
pip install -r requirements.txt
```

## Running Tests

### Run All Integration Tests

```bash
python -m pytest tests/integration/ -v
```

### Run Specific Test Files

```bash
# Test basic commands
python -m pytest tests/integration/test_commands.py -v

# Test download flow
python -m pytest tests/integration/test_download.py -v

# Test inline queries
python -m pytest tests/integration/test_inline.py -v
```

### Run Specific Test Classes

```bash
python -m pytest tests/integration/test_commands.py::TestBasicCommands -v
```

### Run with Output

```bash
python -m pytest tests/integration/ -v -s
```

The `-s` flag shows print statements, which is useful for seeing test progress.

## Test Types

### 1. Handler Tests (Automated)

These tests simulate Telegram updates and verify handler behavior:

- **test_commands.py**: Tests `/start`, `/help`, and unknown commands
- **test_download.py**: Tests download conversation flow
- **test_inline.py**: Tests inline query handling

These tests mock the `extract_info` function to avoid actual video downloads.

### 2. End-to-End Tests (Manual)

These tests require manual interaction. Enable them by setting:

```bash
# In .env.test
ENABLE_E2E_TESTS=1
```

E2E tests will:
1. Send you a message in Telegram
2. Ask you to perform an action (e.g., send a command)
3. Wait for your response
4. Verify the bot's behavior

**Example:**
```bash
python -m pytest tests/integration/test_commands.py::TestCommandEndToEnd -v
```

## Test Structure

### Base Class

All integration tests inherit from `IntegrationTestBase` which:
- Initializes a real bot application
- Sets up handlers
- Provides helper methods for creating updates
- Manages bot lifecycle (startup/shutdown)

### Helper Methods

```python
# Simulate processing an update
await self.simulate_update(update)

# Send a command to your test chat
await self.send_command("/start")

# Get pending updates
updates = await self.get_updates()

# Clear pending updates
await self.clear_updates()
```

## Writing New Tests

### Basic Test Template

```python
from tests.integration.base import IntegrationTestBase
from telegram import Update, Message, Chat, User

class TestMyFeature(IntegrationTestBase):

    async def test_my_handler(self):
        """Test my handler logic"""
        # Create a mock update
        user = User(
            id=self.test_config.user_id,
            first_name="Test",
            is_bot=False,
            username="testuser"
        )

        chat = Chat(id=self.test_config.chat_id, type="private")

        message = Message(
            message_id=1,
            date=None,
            chat=chat,
            from_user=user,
            text="/mycommand"
        )

        update = Update(update_id=1, message=message)

        # Process the update
        await self.simulate_update(update)

        # Add assertions
        self.assertTrue(some_condition)
```

### Testing with Mocks

```python
from unittest.mock import patch, AsyncMock

async def test_with_mock(self):
    """Test with mocked external dependencies"""
    with patch('dasovbot.handlers.download.extract_info') as mock_extract:
        # Set up mock
        mock_extract.return_value = AsyncMock(
            title="Test",
            webpage_url="https://example.com"
        )

        # Run test
        update = self._create_update("/download https://example.com")
        await self.simulate_update(update)

        # Verify mock was called
        mock_extract.assert_called_once()
```

## Best Practices

1. **Use a separate test bot**: Don't use your production bot for testing
2. **Mock external dependencies**: Mock `extract_info` to avoid downloading videos
3. **Clean up state**: Each test gets a fresh `BotState` instance
4. **Test edge cases**: Empty inputs, invalid URLs, error conditions
5. **Use descriptive names**: Test names should clearly describe what they test
6. **Add print statements**: Help users understand test progress

## Troubleshooting

### "TEST_BOT_TOKEN not set"

Make sure `.env.test` exists and contains your test bot token.

### "Timeout waiting for update"

For E2E tests, ensure you're sending the command within the timeout period (usually 30 seconds).

### Bot not responding

1. Check that your test bot is active (not stopped via @BotFather)
2. Verify your bot token is correct
3. Ensure inline mode is enabled if testing inline queries

### Import errors

Make sure you're running from the project root:
```bash
python -m pytest tests/integration/
```

## CI/CD Integration

For CI/CD pipelines, you can:

1. **Run only unit tests** (no test bot needed):
   ```bash
   python -m pytest tests/ --ignore=tests/integration/
   ```

2. **Run integration tests** with a CI test bot:
   ```bash
   export TEST_BOT_TOKEN="your_ci_bot_token"
   export TEST_USER_ID="your_ci_user_id"
   python -m pytest tests/integration/ -v
   ```

3. **Skip E2E tests** in CI (they require manual interaction):
   ```bash
   # Don't set ENABLE_E2E_TESTS in CI
   python -m pytest tests/integration/ -v
   ```

## Further Reading

- [python-telegram-bot Documentation](https://docs.python-telegram-bot.org/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [pytest Documentation](https://docs.pytest.org/)
