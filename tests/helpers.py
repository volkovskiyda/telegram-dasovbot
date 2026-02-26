from unittest.mock import AsyncMock, MagicMock

from dasovbot.state import BotState


def make_user(id=123, username='testuser'):
    user = MagicMock()
    user.id = id
    user.username = username
    user.to_dict.return_value = {'id': id, 'username': username}
    return user


def make_message(chat_id=123, text='', from_user=None):
    message = AsyncMock()
    message.chat_id = chat_id
    message.text = text
    message.from_user = from_user or make_user(id=chat_id)
    message.text_markdown = text
    return message


def make_inline_query(query='', from_user=None):
    iq = AsyncMock()
    iq.query = query
    iq.from_user = from_user or make_user()
    return iq


def make_chosen_inline_result(result_id='rid', from_user=None, inline_message_id='imid'):
    result = MagicMock()
    result.result_id = result_id
    result.from_user = from_user or make_user()
    result.inline_message_id = inline_message_id
    return result


def make_callback_query(data='', from_user=None, message=None):
    cq = AsyncMock()
    cq.data = data
    cq.from_user = from_user or make_user()
    cq.message = message or make_message()
    return cq


def make_update(**kwargs):
    update = MagicMock()
    update.message = kwargs.get('message')
    update.inline_query = kwargs.get('inline_query')
    update.chosen_inline_result = kwargs.get('chosen_inline_result')
    update.callback_query = kwargs.get('callback_query')
    return update


def make_context(state=None, user_data=None, bot=None):
    context = MagicMock()
    context.bot_data = {'state': state or make_state()}
    context.user_data = user_data if user_data is not None else {}
    context.bot = bot or AsyncMock()
    return context


def make_state(**overrides):
    state = BotState(db=AsyncMock())
    for key, value in overrides.items():
        setattr(state, key, value)
    return state
