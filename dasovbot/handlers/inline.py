import logging
from uuid import uuid4

from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultCachedVideo
from telegram.error import BadRequest

from dasovbot.constants import SOURCE_INLINE
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
    inline_queries[id] = {'url': url, 'upload_date': info.upload_date}

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
        except Exception:
            pass
        return

    results = temporary_inline_query.results
    info = state.videos.get(query)
    if results and not info:
        context.user_data['inline_queries'] = temporary_inline_query.inline_queries
        try:
            await query_obj.answer(results=results, cache_time=1)
        except Exception:
            pass
        return

    info = await extract_info(query, download=False, state=state)
    if not info:
        logger.info("inline_query no info: %s", query)
        try:
            await query_obj.answer(results=[])
        except Exception:
            pass
        return

    entries = info.entries
    inline_queries = {}

    if entries:
        items = [process_info(item) for item in process_entries(entries)]
    else:
        items = [info]

    # Telegram rejects the whole result set when video_file_id is None, so
    # without a cached file_id or a loading animation the item is unsendable
    results = [
        inline_video(item, inline_queries, state.animation_file_id)
        for item in items
        if item.file_id or state.animation_file_id
    ]

    temporary_inline_query.results = results
    temporary_inline_query.inline_queries = inline_queries

    context.user_data['inline_queries'] = inline_queries

    if not results:
        logger.info("inline_query no results: %s", query)
        if len(items) == 1:
            # Queue the download anyway so a repeat query can serve the video
            await _populate_video(query, [str(user.id)], state)

    try:
        await query_obj.answer(results=results, cache_time=1)
    except BadRequest as e:
        logger.error("inline_query BadRequest: %s", query, exc_info=e)
    except Exception as e:
        single_video = len(results) == 1
        logger.error("inline_query answer error: %s, single: %s", query, single_video, exc_info=e)
        if single_video:
            await _populate_video(query, chat_ids=[str(user.id)], state=state)


async def chosen_query(update: Update, context):
    state: BotState = context.bot_data['state']
    inline_result = update.chosen_inline_result
    inline_message_id = inline_result.inline_message_id
    inline_queries = context.user_data.pop('inline_queries', None)

    if not inline_message_id or not inline_queries:
        return
    query_data = inline_queries.get(inline_result.result_id)
    if not query_data:
        return
    if isinstance(query_data, str):
        query = query_data
        upload_date = None
    else:
        query = query_data['url']
        upload_date = query_data.get('upload_date')
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

    title = None
    for tiq in state.temporary_inline_queries.values():
        for result in tiq.results:
            if result.id == inline_result.result_id:
                title = result.title
                break
        if title:
            break
    await append_intent(query, state, inline_message_id=inline_message_id, source=SOURCE_INLINE, title=title, upload_date=upload_date)
    logger.info("%s # chosen_query aint: %s", extract_user(user), query)


async def _populate_video(query: str, chat_ids: list, state: BotState):
    info = state.videos.get(query)
    file_id = info.file_id if info else None
    if file_id:
        return info
    await append_intent(query, state, chat_ids=chat_ids, source=SOURCE_INLINE)
