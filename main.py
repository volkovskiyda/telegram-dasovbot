import os, re, time, json, asyncio, yt_dlp
from utils import ydl_opts, extract_url, now, process_info
from uuid import uuid4
from threading import Thread, Condition
from dotenv import load_dotenv
from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Bot, InlineQueryResultCachedVideo, User, Message
from telegram.ext import filters, Application, CommandHandler, MessageHandler, ContextTypes, InlineQueryHandler, ChosenInlineResultHandler, ConversationHandler

SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW, = range(3)
UNSUBSCRIBE_PLAYLIST, = range(1)
DAS_URL, = range(1)

load_dotenv()

developer_chat_id = os.getenv('DEVELOPER_CHAT_ID')
animation_file_id: str

ydl = yt_dlp.YoutubeDL(ydl_opts)

video_info_file = "config/videos.json"
user_info_file = "config/users.json"
subscription_info_file = "config/subscriptions.json"

videos = {}
users = {}
subscriptions = {}

intents = {}

interval_sec = 60 * 60 # an hour
download_video_condition = Condition()

def write_file(file_path, dict):
    try:
        file = open(file_path, "w", encoding='utf8')
        json.dump(dict, file, indent=1, ensure_ascii=False)
        file.write('\r')
    except:
        pass

def read_file(file_path, dict) -> dict:
    try:
        with open(file_path, "r", encoding='utf8') as file:
            return json.load(file)
    except:
        write_file(file_path, dict)
        return {}

def populate_files():
    interval_sec = 60 * 10 # 10 mins
    while True:
        time.sleep(interval_sec)
        write_file(video_info_file, videos)
        write_file(user_info_file, users)
        write_file(subscription_info_file, subscriptions)

videos = read_file(video_info_file, videos)
users = read_file(user_info_file, users)
subscriptions = read_file(subscription_info_file, subscriptions)

def remove_command_prefix(command: str) -> str:
    return re.sub('^/\w+', '', command).lstrip()

def extract_nested_entries(entries: list) -> list:
    nested_entries = entries[0].get('entries')
    return nested_entries if nested_entries else entries

def extract_user(user: User) -> str:
    return f"{now()} {user.username} ({user.id})"

def extract_info(query: str, download=True) -> dict:
    info = videos.get(query)
    if info and (info.get('file_id') or not download):
        videos[query]['requested'] = now()
        return info
    
    if not info:
        try:
            info = ydl.extract_info(query, download=False)
            url = extract_url(info)
            info_url = videos.get(url)
            if info_url:
                info_url['requested'] = now()
                videos[url] = info_url
                videos[query] = info_url
                return info_url
        except:
            print(f"{now()} # extract_info error: {query}")

    if (not info or not info.get('file_id')) and download:
        try:
            info = ydl.extract_info(query)
        except:
            pass
    return process_info(info)

async def post_process(query: str, info: dict, message: Message, remove_message=True, store_info=True) -> str:
    file_id = message.video.file_id
    filepath = info.get('filepath')
    info['file_id'] = file_id
    if store_info:
        url = extract_url(info)
        info.pop('url', None)
        info.pop('filepath', None)
        info.pop('filename', None)
        info.pop('entries', None)
        videos[query] = info
        videos[url] = info
    if remove_message:
        try:
            await message.delete()
        except:
            pass
    if filepath:
        try:
            os.remove(filepath)
        except:
            pass
    return file_id

def append_intent(query: str, chat_ids: list = [], inline_message_id: str = ''):
    query_intent = intents.get(query)
    intent_priority = query_intent['priority'] if query_intent else 0
    intent_items = query_intent['items'] if query_intent else []
    for intent in intent_items:
        if chat_ids and intent.get('chat_ids') == chat_ids: return
        if inline_message_id and intent.get('inline_message_id') == inline_message_id: return

    priority = 2 if inline_message_id else len(chat_ids)

    intent_items.append({
        'chat_ids': chat_ids,
        'inline_message_id': inline_message_id,
        'priority': priority,
    })
    intents[query] = {
        'priority': intent_priority + priority,
        'items': intent_items,
    }
    with download_video_condition:
        download_video_condition.notify()

def process_intents(bot: Bot):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        if not intents:
            with download_video_condition:
                download_video_condition.wait(interval_sec)
        if not intents:
            time.sleep(5)
            continue
        max_priority = max(intents, key=lambda key: intents[key]['priority'])
        loop.run_until_complete(process_query(bot, max_priority))

def populate_subscriptions():
    while True:
        time.sleep(interval_sec)
        for url in list(subscriptions.keys()):
            chat_ids = subscriptions[url]['chat_ids']
            if chat_ids: populate_playlist(url, chat_ids)
            else: subscriptions.pop(url, None)

def populate_playlist(channel: str, chat_ids: list):
    try:
        info = ydl.extract_info(channel, download=False)
    except:
        print(f"{now()} # populate_playlist error: {channel}")
        return
    entries = info.get('entries')
    if not entries:
        print(f"{now()} # populate_playlist no entries: {channel}")
        return
    for entry in entries[:5]: populate_video(entry, chat_ids)

def populate_video(entry: dict, chat_ids: list):
    query = extract_url(entry)
    info = videos.get(query)
    file_id = info.get('file_id') if info else None
    if file_id: return info
    append_intent(query, chat_ids = chat_ids)

def inline_video(info, inline_query_ids) -> InlineQueryResultCachedVideo:
    id = str(uuid4())
    url = extract_url(info)
    file_id = info.get('file_id')
    video_file_id = file_id or animation_file_id
    reply_markup = InlineKeyboardMarkup([[ InlineKeyboardButton(text='loading', url=url) ]]) if not file_id else None
    inline_query_ids[id] = url

    return InlineQueryResultCachedVideo(
        id=id,
        video_file_id=video_file_id,
        title=info['title'],
        description=info['description'],
        caption=info.get('caption'),
        reply_markup=reply_markup,
    )

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    message = update.message
    username = message.from_user['username']
    await message.reply_text(f"Hey, @{username}.\n"
                                    "Welcome to Download and Share Online Video bot\n"
                                    "Type @dasovbot <video url>\n"
                                    "or /das <video url>\n"
                                    "/help - for more details")

async def help(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "@dasovbot <video url> - download and send video\n"
        "/das <video url> - download video"
    )

async def unknown(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Unknown command. Please type /help for available commands"
    )

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inline_query = update.inline_query
    user = inline_query.from_user
    query = inline_query.query.lstrip()

    print(f"{extract_user(user)} # inline_query: {query}")

    if not query: return

    info = extract_info(query, download=False)
    if not info:
        await inline_query.answer(results=[])
        return

    entries = info.get('entries')
    inline_query_ids = {}

    if entries:
        results = [inline_video(process_info(item), inline_query_ids) for item in extract_nested_entries(entries)]
    else:
        results = [inline_video(info, inline_query_ids)]

    context.user_data['inline_query_ids'] = inline_query_ids

    try:
        await inline_query.answer(results=results, cache_time=10)
    except:
        pass

async def chosen_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inline_result = update.chosen_inline_result
    inline_message_id = inline_result.inline_message_id
    inline_query_ids = context.user_data.pop('inline_query_ids', None)

    if not inline_message_id or not inline_query_ids: return
    query = inline_query_ids[inline_result.result_id]
    if not query: return
    user = inline_result.from_user

    print(f"{extract_user(user)} # chosen_query_strt: {query}")

    info = videos.get(query)
    file_id = info.get('file_id') if info else None
    if file_id:
        await context.bot.edit_message_media(
            media=InputMediaVideo(
                media=file_id,
                caption=info.get('caption'),
            ),
            inline_message_id=inline_message_id,
        )
        videos[query]['requested'] = now()
        print(f"{extract_user(user)} # chosen_query_fnsh: {query}")
        return
    
    append_intent(query, inline_message_id = inline_message_id)
    print(f"{extract_user(user)} # chosen_query_append: {query}")

async def process_query(bot: Bot, query: str) -> dict:
    info = extract_info(query)
    if not info:
        print(f"{now()} # process_query error: {query}")
        intents.pop(query, None)
        return info

    caption = info.get('caption')

    try:
        message = await bot.send_video(
        chat_id=developer_chat_id,
        caption=caption,
        video=info['filepath'],
        duration=info.get('duration'),
        width=info.get('width'),
        height=info.get('height'),
        filename=info['filename'],
        disable_notification=True,
    )
    except:
        intents.pop(query, None)
        return info

    await post_process(query, info, message)

    for intent in intents[query]['items']:
        chat_ids = intent.get('chat_ids')
        inline_message_id = intent.get('inline_message_id')
        if chat_ids:
            for chat_id in chat_ids:
                await bot.send_video(
                    chat_id=chat_id,
                    video=message.video,
                    caption=caption,
                )
        elif inline_message_id:
            await bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=InputMediaVideo(
                    media=message.video,
                    caption=caption,
                ),
            )
    intents.pop(query, None)
    return info

async def populate_animation(bot: Bot):
    query = os.getenv('LOADING_VIDEO_ID')

    info = extract_info(query)

    message = await bot.send_video(
        chat_id=developer_chat_id,
        video=info['filepath'],
        filename=info['filename'],
        duration=info['duration'],
        width=info.get('width'),
        height=info.get('height'),
        caption=info.get('caption'),
        disable_notification=True,
    )

    global animation_file_id
    animation_file_id = await post_process(query, info, message, store_info=False)
    print(f"{now()} # animation_file_id = {animation_file_id}")

def user_subscriptions(chat_id: str) -> dict:
    user_subscriptions = {}

    for url in list(subscriptions):
        subscription = subscriptions[url]
        if subscription['chat_ids'].__contains__(chat_id):
            user_subscriptions[url] = subscription

    return user_subscriptions

async def subscription_list(update: Update, _: ContextTypes.DEFAULT_TYPE):
    message = update.message
    subscription_list = []
    for url, subscription in user_subscriptions(chat_id=message.chat_id).items():
        subscription_list.append(f"[{re.escape(subscription['title'])}]({url})")

    if subscription_list: await message.reply_markdown_v2('\n\n'.join(subscription_list))
    else: await message.reply_text('No active subscriptions')

async def das(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if remove_command_prefix(message.text):
        return await das_url(update, _)
    else:
        await message.reply_text("Enter url")
        return DAS_URL
    
async def das_url(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = message.from_user
    chat_id = message.chat_id
    query = remove_command_prefix(message.text)

    print(f"{extract_user(user)} # das: {query}")

    if not query: return ConversationHandler.END

    info = extract_info(query)
    if not info or info.get('entries'):
        await message.reply_text("Unsupported url", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    video = info.get('file_id') or info.get('filepath')
    if not video:
        await message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    video_message = await message.reply_video(
        video=video,
        filename=info.get('filename'),
        duration=info.get('duration'),
        caption=info.get('caption'),
        width=info.get("width"),
        height=info.get("height"),
        reply_to_message_id=message.id,
    )

    users[str(chat_id)] = user.to_dict()
    await post_process(query, info, video_message, remove_message=False)
    return ConversationHandler.END

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if remove_command_prefix(message.text):
        return await subscribe_url(update, context)
    else:
        await message.reply_text("Enter url")
        return SUBSCRIBE_URL

async def subscribe_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = message.from_user
    query = remove_command_prefix(message.text)
    
    print(f"{extract_user(user)} # subscribe_url: {query}")

    if not query: return ConversationHandler.END

    try:
        info = ydl.extract_info(query, download=False)
        uploader_url = info.get('uploader_url')
        if not uploader_url:
            await message.reply_text("Unsupported url", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        if not uploader_url.startswith(query):
            info = ydl.extract_info(uploader_url, download=False)

        try:
            playlists = f"{uploader_url}/playlists"
            info = ydl.extract_info(playlists, download=False)
        except:
            context.user_data['videos'] = f"{uploader_url}/videos"
            return await subscribe_playlist(update, context)

    except:
        print(f"{extract_user(user)} # subscribe_url_failed: {query}")
        await message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    entries = info.get('entries')
    uploader = info.get('uploader') or info.get('uploader_id')
    uploader_videos = f"{uploader_url}/videos"
    if not entries or not uploader or not uploader_videos: return ConversationHandler.END

    urls = { uploader: uploader_videos }
    for item in entries:
        urls.setdefault(item['title'], extract_url(item))

    context.user_data['urls'] = urls

    await message.reply_text("Select playlist", reply_markup=ReplyKeyboardMarkup(
        [[button] for button in list(urls.keys())], one_time_keyboard=True, input_field_placeholder="Select playlist", resize_keyboard=True
    ))
    return SUBSCRIBE_PLAYLIST
    
async def subscribe_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = message.from_user
    chat_id = message.chat_id
    query = message.text

    urls = context.user_data.pop('urls', None)
    videos = context.user_data.pop('videos', None)
    if urls:
        url = urls.get(query)
    elif videos:
        url = videos
    else:
        url = query

    print(f"{extract_user(user)} # subscribe_playlist: {query} - {url}")

    if not url:
        await message.reply_text("Invalid selection", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    users[str(chat_id)] = user.to_dict()
    subscription = subscriptions.get(url)
    reply_markup=ReplyKeyboardMarkup(
        [['Yes', 'No']], one_time_keyboard=True, input_field_placeholder="Show latest videos?", resize_keyboard=True
    )
    if subscription:
        chat_ids = subscription['chat_ids']
        subscription_info = f"[{re.escape(subscription['title'])}]({url})"
        if chat_id in chat_ids:
            await message.reply_markdown_v2(f"Already subscribed to {subscription_info}", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        else:
            chat_ids.append(chat_id)
            context.user_data['subscription_url'] = url
            await message.reply_markdown_v2(f"Subscribed to {subscription_info}\nShow latest videos?", reply_markup=reply_markup)
            return SUBSCRIBE_SHOW
    elif urls:
        title = query
        uploader = next(iter(urls))
        uploader_videos = urls[uploader]
    else:
        try:
            info = ydl.extract_info(url, download=False)
            uploader_url = info.get('uploader_url')
            title = info.get('title')
            uploader = info.get('uploader') or info.get('uploader_id')
            uploader_videos = f"{uploader_url}/videos"
        except:
            print(f"{extract_user(user)} # subscribe_playlist_failed: {url}")
            await message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

    subscriptions[url] = {
        'chat_ids': [chat_id],
        'title': title,
        'uploader': uploader,
        'uploader_videos': uploader_videos,
    }
    subscription_info = f"[{re.escape(title)}]({url})"
    context.user_data['subscription_url'] = url
    await message.reply_markdown_v2(f"Subscribed to {subscription_info}\nShow latest videos?", reply_markup=reply_markup)
    return SUBSCRIBE_SHOW

async def subscribe_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    chat_id = message.chat_id
    result = message.text.lower() in ['yes', 'true', 'y', 't', '1']

    subscription_url = context.user_data.pop('subscription_url', None)
    reply = await message.reply_text(subscription_url, reply_markup=ReplyKeyboardRemove(), disable_notification=True)
    await reply.delete()

    if result:
        try:
            info = ydl.extract_info(subscription_url, download=False)
            entries = info.get('entries')
            for entry in entries[:5]:
                video = videos.get(extract_url(entry))
                file_id = video.get('file_id') if video else None
                if file_id: await context.bot.send_video(chat_id, file_id, caption=video.get('caption'))
        except:
            pass
    return ConversationHandler.END

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if remove_command_prefix(message.text):
        return await unsubscribe_playlist(update, context)
    else:
        subscriptions = list(user_subscriptions(chat_id=message.chat_id).keys())
        if subscriptions:
            await message.reply_text("Select playlist", reply_markup=ReplyKeyboardMarkup(
                [[button] for button in subscriptions], one_time_keyboard=True, input_field_placeholder="Select playlist", resize_keyboard=True)
            )
            return UNSUBSCRIBE_PLAYLIST
        else:
            await message.reply_text("No subscription found", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

async def unsubscribe_playlist(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = message.from_user
    chat_id = message.chat_id
    query = remove_command_prefix(message.text)

    subscription = subscriptions.get(query)

    print(f"{extract_user(user)} # unsubscribe_playlist: {query}")

    if not subscription:
        await message.reply_text("Invalid selection", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    chat_ids = subscription['chat_ids']
    if chat_id not in chat_ids:
        await message.reply_text("No subscription found", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    chat_ids[:] = (item for item in chat_ids if item != chat_id)
    if not chat_ids: subscriptions.pop(query, None)

    subscription_info = f"[{re.escape(subscription['title'])}]({query})"
    await message.reply_markdown_v2(f"Unsubscribed from {subscription_info}", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    print(f"{extract_user(message.from_user)} # cancel")
    await message.reply_text("Cancelled", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    token = os.getenv('BOT_TOKEN')
    base_url = os.getenv('BASE_URL')
    timeout = os.getenv('READ_TIMEOUT') or 30

    application = (
        Application.builder()
        .token(token)
        .base_url(base_url)
        .read_timeout(float(timeout))
        .concurrent_updates(True)
        .build()
    )
    bot = application.bot

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help))

    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(ChosenInlineResultHandler(chosen_query))
    application.add_handler(CommandHandler(['subscriptions', 'subs'], subscription_list))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['download', 'das', 'dv'], das)],
        states={
            DAS_URL: [MessageHandler(filters.TEXT, das_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['subscribe'], subscribe)],
        states={
            SUBSCRIBE_URL: [MessageHandler(filters.TEXT, subscribe_url)],
            SUBSCRIBE_PLAYLIST: [MessageHandler(filters.TEXT, subscribe_playlist)],
            SUBSCRIBE_SHOW: [MessageHandler(filters.TEXT, subscribe_show)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['unsubscribe'], unsubscribe)],
        states={
            UNSUBSCRIBE_PLAYLIST: [MessageHandler(filters.TEXT, unsubscribe_playlist)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(populate_animation(bot))
    Thread(target=populate_subscriptions, daemon=True).start()
    Thread(target=populate_files, daemon=True).start()
    Thread(target=process_intents, args=(bot,), daemon=True).start()
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
