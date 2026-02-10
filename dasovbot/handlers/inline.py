import logging
from uuid import uuid4

from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultCachedVideo
from telegram.error import BadRequest

from dasovbot.downloader import extract_info, extract_url, process_info, process_entries
from dasovbot.helpers import extract_user, now
from dasovbot.models import VideoInfo, TemporaryInlineQuery
from dasovbot.state import BotState
from dasovbot.services.intent_processor import append_intent

logger = logging.getLogger(__name__)


def inline_video(info: VideoInfo, inline_queries: dict, animation_file_id: str) -> InlineQueryResultCachedVideo:
    id = str(uuid4())
    url = extract_url(info)
    file_id = info.file_id
    video_file_id = file_id or animation_file_id
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(text='loading', url=url)]]) if not file_id else None
    inline_queries[id] = url

    return InlineQueryResultCachedVideo(
        id=id,
        video_file_id=video_file_id,
        title=info.title,
        description=info.description,
        caption=info.caption,
        reply_markup=reply_markup,
    )


async def inline_query_handler(update: Update, context):
    state: BotState = context.bot_data['state']
    query_obj = update.inline_query
    user = query_obj.from_user
    query = query_obj.query.lstrip()

    logger.info("%s # inline_query: %s", extract_user(user), query)

    if not query:
        return

    temporary_inline_query = state.temporary_inline_queries.get(query)
    if not temporary_inline_query:
        temporary_inline_query = TemporaryInlineQuery(timestamp=now())
        state.temporary_inline_queries[query] = temporary_inline_query

    if temporary_inline_query.ignored:
        logger.info("inline_query ignored: %s", query)
        try:
            await query_obj.answer(results=[])
        except:
            pass
        return

    results = temporary_inline_query.results
    info = state.videos.get(query)
    if results and not info:
        context.user_data['inline_queries'] = temporary_inline_query.inline_queries
        try:
            await query_obj.answer(results=results, cache_time=1)
        except:
            pass
        return

    info = await extract_info(query, download=False, state=state)
    if not info:
        logger.info("inline_query no info: %s", query)
        try:
            await query_obj.answer(results=[])
        except:
            pass
        return

    entries = info.entries
    inline_queries = {}

    if entries:
        results = [inline_video(process_info(item), inline_queries, state.animation_file_id) for item in process_entries(entries)]
    else:
        results = [inline_video(info, inline_queries, state.animation_file_id)]

    temporary_inline_query.results = results
    temporary_inline_query.inline_queries = inline_queries

    context.user_data['inline_queries'] = inline_queries

    if not results:
        logger.info("inline_query no results: %s", query)

    try:
        await query_obj.answer(results=results, cache_time=1)
    except BadRequest as e:
        logger.error("inline_query BadRequest: %s", query, exc_info=e)
    except Exception as e:
        single_video = len(results) == 1
        logger.error("inline_query answer error: %s, single: %s", query, single_video, exc_info=e)
        if single_video:
            await _populate_video(query, chat_ids=[user.id], state=state)


async def chosen_query(update: Update, context):
    state: BotState = context.bot_data['state']
    inline_result = update.chosen_inline_result
    inline_message_id = inline_result.inline_message_id
    inline_queries = context.user_data.pop('inline_queries', None)

    if not inline_message_id or not inline_queries:
        return
    query = inline_queries[inline_result.result_id]
    if not query:
        return
    user = inline_result.from_user

    logger.info("%s # chosen_query strt: %s", extract_user(user), query)

    info = state.videos.get(query)
    file_id = info.file_id if info else None
    if file_id:
        await context.bot.edit_message_media(
            media=InputMediaVideo(
                media=file_id,
                caption=info.caption,
            ),
            inline_message_id=inline_message_id,
        )
        logger.info("%s # chosen_query fnsh: %s", extract_user(user), query)
        return

    await append_intent(query, state, inline_message_id=inline_message_id)
    logger.info("%s # chosen_query aint: %s", extract_user(user), query)


async def _populate_video(query: str, chat_ids: list, state: BotState):
    info = state.videos.get(query)
    file_id = info.file_id if info else None
    if file_id:
        return info
    await append_intent(query, state, chat_ids=chat_ids)
