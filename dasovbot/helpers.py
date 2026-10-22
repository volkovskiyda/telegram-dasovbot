import logging
import re
from time import strftime
from uuid import uuid4

from telegram import Bot, User

from dasovbot.constants import DATETIME_FORMAT

logger = logging.getLogger(__name__)


def now() -> str:
    return strftime(DATETIME_FORMAT)


def extract_user(user: User) -> str:
    return f"{now()} {user.username} ({user.id})"


# A host with a dot and a TLD, an optional scheme and port, no whitespace. The
# bar is deliberately low (yt-dlp accepts scheme-less URLs); it only has to
# keep plain words and half-typed text away from a network round trip
_URL_RE = re.compile(r'^(?:https?://)?[\w.-]+\.[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?$', re.IGNORECASE)


def looks_like_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def remove_command_prefix(command: str) -> str:
    return re.sub(r'^/\w+', '', command).lstrip()


async def send_message_developer(bot: Bot, text: str, developer_id: str, notification: bool = True):
    try:
        await bot.send_message(chat_id=developer_id, text=text, disable_notification=not notification)
    except Exception:
        pass


# Telegram rejects messages longer than 4096 characters
TELEGRAM_MESSAGE_LIMIT = 4096


def split_message(lines: list[str], separator: str = '\n', limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Join lines into as few messages as fit Telegram's length limit."""
    messages = []
    current = ''
    for line in lines:
        candidate = f"{current}{separator}{line}" if current else line
        if current and len(candidate) > limit:
            messages.append(current)
            current = line
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def user_subscriptions(chat_id: str, subscriptions: dict) -> dict:
    result = {}
    for url, subscription in subscriptions.copy().items():
        if str(chat_id) in subscription.chat_ids:
            result[str(uuid4())] = {'title': subscription.title, 'url': url}
    return result


def append_playlist(playlists: dict, title: str, url: str, pinned: bool = False):
    # pinned entries (the channel-level Videos/Streams feeds) stay on top of the
    # keyboard in insertion order instead of being sorted with the playlists
    id = str(uuid4())
    playlists[id] = {'title': title, 'url': url, 'pinned': pinned}
