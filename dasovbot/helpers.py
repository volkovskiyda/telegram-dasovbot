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


def remove_command_prefix(command: str) -> str:
    return re.sub(r'^/\w+', '', command).lstrip()


async def send_message_developer(bot: Bot, text: str, developer_id: str, notification: bool = True):
    try:
        await bot.send_message(chat_id=developer_id, text=text, disable_notification=not notification)
    except Exception:
        pass


def user_subscriptions(chat_id: str, subscriptions: dict) -> dict:
    result = {}
    for url, subscription in subscriptions.copy().items():
        if str(chat_id) in subscription.chat_ids:
            result[str(uuid4())] = {'title': subscription.title, 'url': url}
    return result


def append_playlist(playlists: dict, title: str, url: str):
    id = str(uuid4())
    playlists[id] = {'title': title, 'url': url}
