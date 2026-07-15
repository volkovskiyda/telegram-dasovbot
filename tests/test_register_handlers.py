import unittest
from datetime import datetime, timezone

from telegram import Chat, Message, MessageEntity, Update
from telegram.ext import Application, ConversationHandler, MessageHandler

from dasovbot.handlers import register_handlers


def make_update(text: str, command: bool = False) -> Update:
    entities = []
    if command:
        entities = [MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(text.split()[0]))]
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type='private'),
        text=text,
        entities=entities,
    )
    return Update(update_id=1, message=message)


class TestRegisterHandlers(unittest.TestCase):
    def setUp(self):
        self.app = Application.builder().token('123:TEST').build()
        register_handlers(self.app)

    def _conversation_message_handlers(self):
        for group in self.app.handlers.values():
            for handler in group:
                if isinstance(handler, ConversationHandler):
                    for state_handlers in handler.states.values():
                        for state_handler in state_handlers:
                            if isinstance(state_handler, MessageHandler):
                                yield state_handler

    def test_conversation_text_states_ignore_commands(self):
        handlers = list(self._conversation_message_handlers())
        self.assertTrue(handlers)
        cancel = make_update('/cancel', command=True)
        url = make_update('https://example.com/v')
        for handler in handlers:
            self.assertFalse(handler.check_update(cancel))
            self.assertTrue(handler.check_update(url))


if __name__ == '__main__':
    unittest.main()
