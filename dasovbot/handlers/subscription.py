import asyncio
import logging
from functools import partial
from uuid import uuid4

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import ConversationHandler

from dasovbot.constants import (
    SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW,
    UNSUBSCRIBE_PLAYLIST, MULTIPLE_SUBSCRIBE_URLS, SOURCE_SUBSCRIPTION,
)
from dasovbot.downloader import extract_url, get_ydl
from dasovbot.helpers import (
    extract_user, remove_command_prefix, user_subscriptions, append_playlist,
)
from dasovbot.models import Subscription
from dasovbot.services.intent_processor import append_intent
from dasovbot.state import BotState

logger = logging.getLogger(__name__)

MAX_PAGE_BYTES = 4096
BUTTON_OVERHEAD = 60


def _extract_info_sync(query: str) -> dict:
    # Create, use and close the YoutubeDL inside the executor thread:
    # un-closed instances permanently retain HTTP sessions and SSL contexts.
    ydl = get_ydl()
    try:
        return ydl.extract_info(query, download=False)
    finally:
        ydl.close()


async def _extract_info(query: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_extract_info_sync, query))


def _button_size(text: str, callback_data: str) -> int:
    return len(text.encode('utf-8')) + len(callback_data.encode('utf-8')) + BUTTON_OVERHEAD


def _paginate_items(items: list[tuple[str, dict]]) -> list[list[tuple[str, dict]]]:
    sorted_items = sorted(items, key=lambda item: len(item[1]['title']))

    pages = []
    current_page = []
    current_size = 0
    nav_reserve = _button_size('< Prev', 'page:99') + _button_size('Next >', 'page:99') + _button_size('99/99', 'noop')
    cancel_size = _button_size('Cancel', 'cancel')
    budget = MAX_PAGE_BYTES - cancel_size - nav_reserve

    for item in sorted_items:
        id, data = item
        size = _button_size(data['title'], id)
        if current_page and current_size + size > budget:
            pages.append(current_page)
            current_page = []
            current_size = 0
        current_page.append(item)
        current_size += size

    if current_page:
        pages.append(current_page)
    return pages or [[]]


def build_paginated_keyboard(items: dict, page: int) -> list[list[InlineKeyboardButton]]:
    pages = _paginate_items(list(items.items()))
    total_pages = len(pages)
    page = max(0, min(page, total_pages - 1))
    page_items = pages[page]

    keyboard = [[InlineKeyboardButton(text=item['title'], callback_data=id)] for id, item in page_items]

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text='< Prev', callback_data=f'page:{page - 1}'))
        nav_row.append(InlineKeyboardButton(text=f'{page + 1}/{total_pages}', callback_data='noop'))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text='Next >', callback_data=f'page:{page + 1}'))
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(text='Cancel', callback_data='cancel')])
    return keyboard


async def subscription_list(update: Update, context):
    state: BotState = context.bot_data['state']
    message = update.message
    subs = user_subscriptions(message.chat_id, state.subscriptions)
    sub_list = [f"[{item['title'].replace('[', '').replace(']', '')}]({item['url']})" for item in subs.values()]

    try:
        if sub_list:
            await message.reply_markdown('\n\n'.join(sub_list))
        else:
            await message.reply_text('No subscriptions')
    except Exception:
        pass


async def subscribe(update: Update, context) -> int:
    message = update.message
    if remove_command_prefix(message.text):
        return await subscribe_url(update, context)
    else:
        await message.reply_text("Enter url")
        return SUBSCRIBE_URL


async def subscribe_url(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    message = update.message
    user = message.from_user
    query = remove_command_prefix(message.text)

    logger.info("%s # subscribe_url: %s", extract_user(user), query)

    if not query:
        return ConversationHandler.END

    try:
        info = await _extract_info(query)
        uploader_url = info.get('uploader_url')
        if not uploader_url:
            await message.reply_text("Unsupported url", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        if not uploader_url.startswith(query):
            await _extract_info(uploader_url)

        try:
            playlists_url = f"{uploader_url}/playlists"
            info = await _extract_info(playlists_url)
        except Exception:
            context.user_data['uploader_videos'] = f"{uploader_url}/videos"
            return await subscribe_playlist(update, context)

    except Exception:
        logger.error("%s # subscribe_url failed: %s", extract_user(user), query)
        await message.reply_text("Error occurred", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    entries = info.get('entries')
    uploader = info.get('uploader') or info.get('uploader_id')
    uploader_videos = f"{uploader_url}/videos"
    if not entries or not uploader or not uploader_videos:
        await message.reply_text("Error occurred", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    playlists = {}
    append_playlist(playlists, f"{uploader} Videos", uploader_videos)
    try:
        uploader_streams = f"{uploader_url}/streams"
        await _extract_info(uploader_streams)
        append_playlist(playlists, f"{uploader} Streams", uploader_streams)
    except Exception:
        pass

    for entry in entries:
        url = extract_url(entry)
        append_playlist(playlists, entry['title'], url)
        if query == url:
            return await subscribe_playlist(update, context)

    context.user_data['playlists'] = playlists
    await message.reply_markdown(
        f"Select playlist of [{uploader}]({uploader_url})",
        reply_markup=InlineKeyboardMarkup(build_paginated_keyboard(playlists, 0))
    )
    return SUBSCRIBE_PLAYLIST


async def subscribe_playlist(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    callback_query = update.callback_query

    if callback_query:
        await callback_query.answer()
        message = callback_query.message
        callback_data = callback_query.data
        if callback_data == 'cancel':
            context.user_data.pop('playlists', None)
            context.user_data.pop('uploader_videos', None)
            try:
                await message.delete()
            except Exception:
                pass
            return ConversationHandler.END
        # Keep 'playlists' in user_data on nav taps: popping it here would
        # break the selection that follows
        if callback_data == 'noop':
            return SUBSCRIBE_PLAYLIST
        if callback_data.startswith('page:') and context.user_data.get('playlists'):
            playlists = context.user_data['playlists']
            page = int(callback_data.split(':')[1])
            await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(build_paginated_keyboard(playlists, page)))
            return SUBSCRIBE_PLAYLIST
        playlists = context.user_data.pop('playlists', None)
        uploader_videos = context.user_data.pop('uploader_videos', None)
        user = callback_query.from_user
        message_text = message.edit_text

        if not playlists:
            logger.error("%s # subscribe_playlist failed", extract_user(user))
            await message_text("Error occurred", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END

        playlist = playlists.get(callback_data)
        if not playlist:
            # Stale keyboard: the playlist mapping was rebuilt with new keys
            logger.warning("%s # subscribe_playlist invalid selection: %s", extract_user(user), callback_data)
            await message_text("Invalid selection", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END
        title = playlist['title']
        url = playlist['url']
    else:
        playlists = context.user_data.pop('playlists', None)
        uploader_videos = context.user_data.pop('uploader_videos', None)
        message = update.message
        user = message.from_user
        message_text = message.reply_text
        title = None
        if uploader_videos:
            url = uploader_videos
        else:
            url = remove_command_prefix(message.text)

    logger.info("%s # subscribe_playlist: %s - %s", extract_user(user), title, url)

    if not url:
        await message_text("Invalid selection", reply_markup=InlineKeyboardMarkup([]))
        return ConversationHandler.END

    chat_id = str(message.chat_id)
    await state.set_user(chat_id, user.to_dict())
    subscription = state.subscriptions.get(url)
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text='Yes', callback_data='True'), InlineKeyboardButton(text='No', callback_data='False')]]
    )
    if subscription:
        chat_ids = subscription.chat_ids
        subscription_info = f"[{subscription.title}]({url})"
        if chat_id in chat_ids:
            await message_text(f"Already subscribed to {subscription_info}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END
        else:
            await state.add_subscriber(url, chat_id)
            context.user_data['subscription_url'] = url
            await message_text(f"Subscribed to {subscription_info}\nShow latest videos?", parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            return SUBSCRIBE_SHOW
    elif playlists:
        uploader_info = next(iter(playlists.values()))
        uploader = uploader_info['title']
        uploader_videos = uploader_info['url']
    else:
        try:
            info = await _extract_info(url)
            uploader_url = info.get('uploader_url')
            uploader = info.get('uploader') or info.get('uploader_id') or ''
            # Some extractors return no title: fall back so Markdown links
            # and the dashboard never render a None title
            title = info.get('title') or uploader or url
            uploader_videos = f"{uploader_url}/videos"
        except Exception:
            logger.error("%s # subscribe_playlist failed: %s", extract_user(user), url, exc_info=True)
            await message_text("Error occurred", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END

    await state.set_subscription(url, Subscription(
        chat_ids=[chat_id],
        title=title,
        uploader=uploader,
        uploader_videos=uploader_videos,
    ))
    subscription_info = f"[{title}]({url})"
    context.user_data['subscription_url'] = url
    await message_text(f"Subscribed to {subscription_info}\nShow latest videos?", parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    return SUBSCRIBE_SHOW


async def subscribe_show(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    callback_query = update.callback_query
    message = callback_query.message
    chat_id = message.chat_id
    result = callback_query.data == 'True'
    text = '\n'.join(message.text_markdown.split('\n')[:-1])

    await callback_query.answer()
    await message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([]))
    subscription_url = context.user_data.pop('subscription_url', None)

    if result:
        try:
            info = await _extract_info(subscription_url)
            entries = info.get('entries') or []
            for entry in entries[:5]:
                url = extract_url(entry)
                video = state.videos.get(url)
                file_id = video.file_id if video else None
                if file_id:
                    await context.bot.send_video(chat_id, file_id, caption=video.caption)
                else:
                    # Not cached yet: enqueue so the intent worker downloads it
                    # and posts to this chat, instead of silently skipping it.
                    await append_intent(url, state, chat_ids=[str(chat_id)], source=SOURCE_SUBSCRIPTION,
                                        title=entry.get('title'), upload_date=entry.get('upload_date'))
        except Exception:
            logger.error("subscribe_show error: %s", subscription_url, exc_info=True)
    return ConversationHandler.END


async def unsubscribe(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    message = update.message
    if remove_command_prefix(message.text):
        return await unsubscribe_playlist(update, context)
    else:
        subs = user_subscriptions(message.chat_id, state.subscriptions)
        if subs:
            context.user_data['user_subscriptions'] = subs
            await message.reply_text("Select playlist", reply_markup=InlineKeyboardMarkup(
                build_paginated_keyboard(subs, 0)
            ))
            return UNSUBSCRIBE_PLAYLIST
        else:
            await message.reply_text("No subscription found")
            return ConversationHandler.END


async def unsubscribe_playlist(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    callback_query = update.callback_query
    if callback_query:
        await callback_query.answer()
        message = callback_query.message
        callback_data = callback_query.data
        if callback_data == 'cancel':
            try:
                await message.delete()
            except Exception:
                pass
            return ConversationHandler.END
        if callback_data == 'noop':
            return UNSUBSCRIBE_PLAYLIST
        if callback_data.startswith('page:'):
            page = int(callback_data.split(':')[1])
            user_subs = context.user_data.get('user_subscriptions', {})
            await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(build_paginated_keyboard(user_subs, page)))
            return UNSUBSCRIBE_PLAYLIST
        user = callback_query.from_user
        message_text = message.edit_text
        user_subs = context.user_data.pop('user_subscriptions', None)

        if not user_subs:
            logger.error("%s # unsubscribe_playlist failed", extract_user(user))
            await message_text("Error occurred", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END

        entry = user_subs.get(callback_data)
        if not entry:
            # Stale keyboard: the subscription mapping was rebuilt with new keys
            logger.warning("%s # unsubscribe_playlist invalid selection: %s", extract_user(user), callback_data)
            await message_text("Invalid selection", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END
        query = entry['url']
    else:
        message = update.message
        user = message.from_user
        message_text = message.reply_text
        query = remove_command_prefix(message.text)

    chat_id = str(message.chat_id)
    subscription = state.subscriptions.get(query)

    logger.info("%s # unsubscribe_playlist: %s", extract_user(user), query)

    if not subscription:
        await message_text("Invalid selection", reply_markup=InlineKeyboardMarkup([]))
        return ConversationHandler.END

    chat_ids = subscription.chat_ids
    if chat_id not in chat_ids:
        await message_text("No subscription found", reply_markup=InlineKeyboardMarkup([]))
        return ConversationHandler.END

    await state.remove_subscriber(query, chat_id)

    subscription_info = f"[{subscription.title}]({query})"
    await message_text(f"Unsubscribed from {subscription_info}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([]))
    return ConversationHandler.END


async def playlists(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    message = update.message
    sub_list = [item['url'] for item in user_subscriptions(message.chat_id, state.subscriptions).values()]

    try:
        if not sub_list:
            await message.reply_text('No subscriptions')
            return ConversationHandler.END
    except Exception:
        pass

    videos = []
    streams = []
    both = []
    already_processed = []
    for subscription in sub_list:
        if subscription in already_processed:
            continue
        try:
            info = await _extract_info(subscription)
            uploader_url = info.get('uploader_url')
            if not uploader_url:
                continue
            uploader_videos = f"{uploader_url}/videos"
            uploader_streams = f"{uploader_url}/streams"
            subscription_videos = uploader_url in sub_list or uploader_videos in sub_list
            subscription_streams = uploader_streams in sub_list
            if subscription_videos and subscription_streams:
                both.append(uploader_url)
                already_processed.append(uploader_url)
                already_processed.append(uploader_videos)
                already_processed.append(uploader_streams)
            elif subscription_videos:
                try:
                    await _extract_info(uploader_streams)
                    streams.append(uploader_streams)
                except Exception:
                    pass
            elif subscription_streams:
                try:
                    await _extract_info(uploader_videos)
                    videos.append(uploader_videos)
                except Exception:
                    pass
        except Exception:
            pass
    if videos:
        output = [f"{item}" for item in videos]
        output.insert(0, "Available *Videos*")
        await message.reply_text('\n'.join(output))
    if streams:
        output = [f"{item}" for item in streams]
        output.insert(0, "Available *Streams*")
        await message.reply_text('\n'.join(output))
    if both:
        output = [f"{item}" for item in both]
        output.insert(0, "*Videos and Streams*")
        await message.reply_text('\n'.join(output))
    return ConversationHandler.END


async def multiple_subscribe(update: Update, _) -> int:
    await update.message.reply_text("Enter urls")
    return MULTIPLE_SUBSCRIBE_URLS


async def multiple_subscribe_urls(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    message = update.message
    user = message.from_user
    query = message.text
    chat_id = str(message.chat_id)

    if not query:
        return ConversationHandler.END

    urls = query.strip('\n').split('\n')
    subscribed = []
    already_subscribed = []
    failed = []

    logger.info("%s # multiple_subscribe: %s urls", extract_user(user), len(urls))

    for url in urls:
        url = url.strip()
        if not url:
            failed.append(url)
            continue
        subscription = state.subscriptions.get(url)
        if subscription:
            if chat_id in subscription.chat_ids:
                already_subscribed.append(url)
                continue
            else:
                await state.add_subscriber(url, chat_id)
                subscribed.append(url)
        else:
            try:
                info = await _extract_info(url)
                uploader = info.get('uploader') or info.get('uploader_id') or ''
                title = info.get('title') or uploader or url
            except Exception:
                failed.append(url)
                continue
            await state.set_subscription(url, Subscription(
                chat_ids=[chat_id],
                title=title,
                uploader=uploader,
                uploader_videos=url,
            ))
            subscribed.append(url)
    if already_subscribed:
        await message.reply_text('\n'.join(["Already Subscribed"] + [f"{item}" for item in already_subscribed]))
    if failed:
        await message.reply_text('\n'.join(["Failed subscriptions"] + [f"{item}" for item in failed]))
    logger.info("%s # multiple_subscribe len: %s, already_subscribed: %s, failed: %s", extract_user(user), len(urls), len(already_subscribed), len(failed))
    await message.reply_text("Multiple Subscribe" + (f"\n{len(subscribed)} urls successfully" if subscribed else "") + (f"\n{len(failed)} urls failed" if failed else "") + (f"\n{len(already_subscribed)} urls already subscribed" if already_subscribed else ""))
    return ConversationHandler.END
