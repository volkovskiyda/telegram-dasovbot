import unittest
from datetime import datetime, timezone

from telegram import Chat, Message, MessageEntity, Update
from telegram.ext import Application, ConversationHandler, MessageHandler, TypeHandler

from dasovbot.constants import CONVERSATION_TIMEOUT_SEC
from dasovbot.handlers import register_handlers
from dasovbot.handlers.ban import guard_banned


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

    def test_conversations_time_out(self):
        conversations = [
            handler
            for group in self.app.handlers.values()
            for handler in group
            if isinstance(handler, ConversationHandler)
        ]
        self.assertEqual(len(conversations), 4)
        for handler in conversations:
            self.assertEqual(handler.conversation_timeout, CONVERSATION_TIMEOUT_SEC)

    def test_ban_guard_runs_first_for_every_update(self):
        # Group -1 runs before the handlers in group 0, so a banned user's
        # update is stopped before any of them can hand out a video
        self.assertEqual(min(self.app.handlers), -1)
        guards = [handler for handler in self.app.handlers[-1] if isinstance(handler, TypeHandler)]
        self.assertEqual([handler.callback for handler in guards], [guard_banned])
        self.assertTrue(guards[0].check_update(make_update('/start', command=True)))
        self.assertTrue(guards[0].check_update(Update(update_id=2)))

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
