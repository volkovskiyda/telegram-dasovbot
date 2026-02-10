import logging
from uuid import uuid4

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import ConversationHandler

from dasovbot.constants import (
    SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW,
    UNSUBSCRIBE_PLAYLIST, MULTIPLE_SUBSCRIBE_URLS,
)
from dasovbot.downloader import extract_url, get_ydl
from dasovbot.helpers import (
    extract_user, remove_command_prefix, user_subscriptions, append_playlist,
)
from dasovbot.models import Subscription
from dasovbot.state import BotState

logger = logging.getLogger(__name__)


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
    except:
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
    ydl = get_ydl()
    message = update.message
    user = message.from_user
    query = remove_command_prefix(message.text)

    logger.info("%s # subscribe_url: %s", extract_user(user), query)

    if not query:
        return ConversationHandler.END

    try:
        info = ydl.extract_info(query, download=False)
        uploader_url = info.get('uploader_url')
        if not uploader_url:
            await message.reply_text("Unsupported url", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        if not uploader_url.startswith(query):
            ydl.extract_info(uploader_url, download=False)

        try:
            playlists_url = f"{uploader_url}/playlists"
            info = ydl.extract_info(playlists_url, download=False)
        except:
            context.user_data['uploader_videos'] = f"{uploader_url}/videos"
            return await subscribe_playlist(update, context)

    except:
        logger.error("%s # subscribe_url failed: %s", extract_user(user), query)
        await message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    entries = info.get('entries')
    uploader = info.get('uploader') or info.get('uploader_id')
    uploader_videos = f"{uploader_url}/videos"
    if not entries or not uploader or not uploader_videos:
        await message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    playlists = {}
    append_playlist(playlists, f"{uploader} Videos", uploader_videos)
    try:
        uploader_streams = f"{uploader_url}/streams"
        ydl.extract_info(uploader_streams, download=False)
        append_playlist(playlists, f"{uploader} Streams", uploader_streams)
    except:
        pass

    for entry in entries:
        url = extract_url(entry)
        append_playlist(playlists, entry['title'], url)
        if query == url:
            return await subscribe_playlist(update, context)

    context.user_data['playlists'] = playlists
    await message.reply_markdown(
        f"Select playlist of [{uploader}]({uploader_url})",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text=item['title'], callback_data=id)] for id, item in playlists.items()]
            + [[InlineKeyboardButton(text='Cancel', callback_data='cancel')]]
        )
    )
    return SUBSCRIBE_PLAYLIST


async def subscribe_playlist(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    ydl = get_ydl()
    playlists = context.user_data.pop('playlists', None)
    uploader_videos = context.user_data.pop('uploader_videos', None)
    callback_query = update.callback_query

    if callback_query:
        await callback_query.answer()
        message = callback_query.message
        callback_data = callback_query.data
        if callback_data == 'cancel':
            try:
                await message.delete()
            except:
                pass
            return ConversationHandler.END
        user = callback_query.from_user
        message_text = message.edit_text

        if not playlists:
            logger.error("%s # subscribe_playlist failed", extract_user(user))
            await message_text("Error occured", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END

        playlist = playlists[callback_data]
        title = playlist['title']
        url = playlist['url']
    else:
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
    state.users[chat_id] = user.to_dict()
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
            chat_ids.append(chat_id)
            context.user_data['subscription_url'] = url
            await message_text(f"Subscribed to {subscription_info}\nShow latest videos?", parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            return SUBSCRIBE_SHOW
    elif playlists:
        uploader_info = next(iter(playlists.values()))
        uploader = uploader_info['title']
        uploader_videos = uploader_info['url']
    else:
        try:
            info = ydl.extract_info(url, download=False)
            uploader_url = info.get('uploader_url')
            title = info.get('title')
            uploader = info.get('uploader') or info.get('uploader_id')
            uploader_videos = f"{uploader_url}/videos"
        except:
            logger.error("%s # subscribe_playlist failed: %s", extract_user(user), url)
            await message_text("Error occured", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END

    state.subscriptions[url] = Subscription(
        chat_ids=[chat_id],
        title=title,
        uploader=uploader,
        uploader_videos=uploader_videos,
    )
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
            ydl = get_ydl()
            info = ydl.extract_info(subscription_url, download=False)
            entries = info.get('entries')
            for entry in entries[:5]:
                video = state.videos.get(extract_url(entry))
                file_id = video.file_id if video else None
                if file_id:
                    await context.bot.send_video(chat_id, file_id, caption=video.caption)
        except:
            pass
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
                [[InlineKeyboardButton(text=item['title'], callback_data=id)] for id, item in subs.items()]
                + [[InlineKeyboardButton(text='Cancel', callback_data='cancel')]]
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
            except:
                pass
            return ConversationHandler.END
        user = callback_query.from_user
        message_text = message.edit_text
        user_subs = context.user_data.pop('user_subscriptions', None)

        if not user_subs:
            logger.error("%s # unsubscribe_playlist failed", extract_user(user))
            await message_text("Error occured", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END

        query = user_subs[callback_data]['url']
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

    chat_ids[:] = (item for item in chat_ids if item != chat_id)
    if not chat_ids:
        state.subscriptions.pop(query, None)

    subscription_info = f"[{subscription.title}]({query})"
    await message_text(f"Unsubscribed from {subscription_info}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([]))
    return ConversationHandler.END


async def playlists(update: Update, context) -> int:
    state: BotState = context.bot_data['state']
    ydl = get_ydl()
    message = update.message
    sub_list = [item['url'] for item in user_subscriptions(message.chat_id, state.subscriptions).values()]

    try:
        if not sub_list:
            await message.reply_text('No subscriptions')
            return ConversationHandler.END
    except:
        pass

    videos = []
    streams = []
    both = []
    already_processed = []
    for subscription in sub_list:
        if subscription in already_processed:
            continue
        try:
            info = ydl.extract_info(subscription, download=False)
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
                    ydl.extract_info(uploader_streams, download=False)
                    streams.append(uploader_streams)
                except:
                    pass
            elif subscription_streams:
                try:
                    ydl.extract_info(uploader_videos, download=False)
                    videos.append(uploader_videos)
                except:
                    pass
        except:
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
    ydl = get_ydl()
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
            chat_ids = subscription.chat_ids
            if chat_id in chat_ids:
                already_subscribed.append(url)
                continue
            else:
                chat_ids.append(chat_id)
                subscribed.append(url)
        else:
            try:
                info = ydl.extract_info(url, download=False)
                title = info.get('title')
                uploader = info.get('uploader') or info.get('uploader_id')
            except:
                failed.append(url)
                continue
            state.subscriptions[url] = Subscription(
                chat_ids=[chat_id],
                title=title,
                uploader=uploader,
                uploader_videos=url,
            )
            subscribed.append(url)
    if already_subscribed:
        await message.reply_text('\n'.join(["Already Subscribed"] + [f"{item}" for item in already_subscribed]))
    if failed:
        await message.reply_text('\n'.join(["Failed subscriptions"] + [f"{item}" for item in failed]))
    logger.info("%s # multiple_subscribe len: %s, already_subscribed: %s, failed: %s", extract_user(user), len(urls), len(already_subscribed), len(failed))
    await message.reply_text(f"Multiple Subscribe" + (f"\n{len(subscribed)} urls successfully" if subscribed else "") + (f"\n{len(failed)} urls failed" if failed else "") + (f"\n{len(already_subscribed)} urls already subscribed" if already_subscribed else ""))
    return ConversationHandler.END
