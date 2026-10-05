import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop

from dasovbot.constants import SOURCE_INLINE
from dasovbot.handlers.inline import lookup_query_data
from dasovbot.helpers import extract_user
from dasovbot.services.ban import schedule_fake_failure
from dasovbot.state import BotState

logger = logging.getLogger(__name__)


async def guard_banned(update: Update, context):
    """Group -1 handler: stop a banned user's inline updates before any handler sees them.

    Inline queries and chosen results are stateless, so they can be answered
    here, like dead videos, for every present or future inline path. Messages
    pass through: whether a URL is a download or a subscription depends on
    conversation state only the ConversationHandlers know, and banned users
    keep managing subscriptions. The two message paths that would hand out a
    video check the ban themselves (download_url via fake_download,
    subscribe_show), and deliveries already queued are caught by
    process_intent.
    """
    state: BotState = context.bot_data['state']
    user = update.effective_user
    if not user or not state.is_banned(user.id):
        return

    if update.inline_query:
        # Never answered: the client keeps loading until Telegram times the
        # query out and shows no results. Skipping the cache also keeps the
        # banned user from picking up other users' cached file_ids
        logger.info("%s # inline_query banned: %s", extract_user(user), update.inline_query.query.lstrip())
        raise ApplicationHandlerStop

    if update.chosen_inline_result:
        result = update.chosen_inline_result
        inline_queries = context.user_data.pop('inline_queries', None)
        query_data = (inline_queries or {}).get(result.result_id) or lookup_query_data(state, result.result_id)
        query = query_data if isinstance(query_data, str) else (query_data or {}).get('url')
        if query and result.inline_message_id:
            # A result answered before the ban: fail its placeholder like a dead video
            schedule_fake_failure(context.bot, state, query, inline_message_id=result.inline_message_id)
            logger.info("%s # chosen_query banned: %s", extract_user(user), query)
            await state.record_request(user.id, query, SOURCE_INLINE)
        raise ApplicationHandlerStop
