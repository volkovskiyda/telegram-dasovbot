import os, time, json, asyncio, yt_dlp
from utils import ydl_opts, extract_url, now, process_info
from uuid import uuid4
from threading import Thread, Condition
from dotenv import load_dotenv
from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Bot, InlineQueryResultCachedVideo, User, Message
from telegram.ext import filters, Application, CommandHandler, MessageHandler, ContextTypes, InlineQueryHandler, ChosenInlineResultHandler, ConversationHandler

SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST = range(2)

load_dotenv()

developer_chat_id = os.getenv('DEVELOPER_CHAT_ID')
animation_file_id: str

ydl = yt_dlp.YoutubeDL(ydl_opts)

video_info_file = "config/videos.json"
user_info_file = "config/users.json"
subscription_info_file = "config/subscriptions.json"
populate_channels_file = "config/download.txt"

videos = {}
users = {}
subscriptions = {}

intents = {}
inline_query_ids = {}

populate_channels_interval_sec = 60 * 60 # an hour
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

def extract_uploader_entries(entries: list) -> list:
    if not entries: return []
    first_entries = entries[0]
    if not first_entries: return entries
    nested_entries = first_entries.get('entries')
    if not nested_entries: return entries
    webpage_url = first_entries.get('webpage_url')
    if not webpage_url: return entries
    info = ydl.extract_info(webpage_url, download=False)
    return info.get('entries')

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
        del info['url']
        del info['filepath']
        del info['filename']
        del info['entries']
        videos[query] = info
        videos[url] = info
    if remove_message: await message.delete()
    if filepath: os.remove(filepath)
    return file_id

def append_intent(query: str, inline_message_id: str = '', priority: int = 1):
    query_intent = intents.get(query)
    intent_priority = query_intent['priority'] if query_intent else 0
    intent_items = query_intent['items'] if query_intent else []
    for intent in intent_items:
        if intent.get('inline_message_id') == inline_message_id:
            return
    intent_items.append({
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
    while True:
        if not intents:
            with download_video_condition:
                download_video_condition.wait(populate_channels_interval_sec)
        if not intents:
            time.sleep(5)
            continue
        max_priority = max(intents, key=lambda key: intents[key]['priority'])
        asyncio.new_event_loop().run_until_complete(process_query(bot, max_priority))

def populate_channels():
    while True:
        try:
            with open(populate_channels_file, "r") as file:
                channels = [line.rstrip() for line in file]
                for channel in channels:
                    populate_channel(channel)
        except:
            pass
        finally:
            time.sleep(populate_channels_interval_sec)

def populate_channel(channel: str):
    info = ydl.extract_info(channel, download=False)
    entries = info.get('entries')
    uploader_url = info.get('uploader_url')
    if not entries:
        if not uploader_url: return
        info = ydl.extract_info(uploader_url, download=False)
        entries = info.get('entries')
    entries = extract_uploader_entries(entries)
    if not entries: return
    for entry in entries[:5]:
        populate_video(entry)

def populate_video(entry: dict) -> dict:
    query = extract_url(entry)
    info = videos.get(query)
    file_id = info.get('file_id') if info else None
    if file_id: return info
    append_intent(query)

def inline_video(info) -> InlineQueryResultCachedVideo:
    id = str(uuid4())
    url = extract_url(info)
    inline_query_ids[id] = url
    file_id = info.get('file_id')

    reply_markup = InlineKeyboardMarkup([[ InlineKeyboardButton(text='loading', url=url) ]]) if not file_id else None
    return InlineQueryResultCachedVideo(
        id=id,
        video_file_id=file_id or animation_file_id,
        title=info['title'],
        description=info['description'],
        caption=info.get('caption'),
        reply_markup=reply_markup
    )

async def start_command(update: Update, _: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user['username']
    await update.message.reply_text(f"Hey, @{username}.\n"
                                    "Welcome to Download and Share Online Video bot\n"
                                    "Type @dasovbot <video url>\n"
                                    "or /das <video url>\n"
                                    "/help - for more details")

async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "@dasovbot <video url> - download and send video\n"
        "/das <video url> - download video"
    )

async def unknown_command(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Unknown command. Please type /help for available commands"
    )

async def das_command(update: Update, _: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    query = message.text.removeprefix('/das').removeprefix('/dv').lstrip()

    print(f"{extract_user(user)} # das: {query}")

    if not query:
        await update.message.reply_text('Type /das <video url>')
        return

    info = extract_info(query)
    if not info or info.get('entries'): return

    video = info.get('file_id') or info.get('filepath')
    if not video: return

    message = await update.message.reply_video(
        video=video,
        filename=info.get('filename'),
        duration=info.get('duration'),
        caption=info.get('caption'),
        width=info.get("width"),
        height=info.get("height"),
        reply_to_message_id=update.message.id,
    )

    await post_process(query, info, message, remove_message=False)

async def inline_query(update: Update, _: ContextTypes.DEFAULT_TYPE):
    inline_query = update.inline_query
    user = inline_query.from_user
    query = inline_query.query.lstrip()

    print(f"{extract_user(user)} # inline_query: {query}")

    if not query: return

    info = extract_info(query, download=False)
    if not info:
        await update.inline_query.answer(results=[])
        return

    entries = info.get('entries')

    if entries:
        results = [inline_video(item) for item in extract_nested_entries(entries)]
    else:
        results = [inline_video(info)]

    await update.inline_query.answer(results=results, cache_time=10)

async def chosen_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inline_result = update.chosen_inline_result
    inline_message_id = inline_result.inline_message_id
    
    if not inline_message_id: return

    query = inline_query_ids.pop(inline_result.result_id)
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
    
    append_intent(query, inline_message_id, priority=2)
    print(f"{extract_user(user)} # chosen_query_append: {query}")

async def process_query(bot: Bot, query: str) -> dict:
    info = extract_info(query)
    if not info:
        print(f"{now()} # process_query error: {query}")
        del intents[query]
        return info

    duration = info.get('duration')
    width = info.get('width')
    height = info.get('height')
    thumbnail = info['thumbnail']
    caption = info.get('caption')
    filepath = info['filepath']
    filename = info['filename']

    try:
        message = await bot.send_video(
        chat_id=developer_chat_id,
        video=filepath,
        duration=duration,
        width=width,
        height=height,
        caption=caption,
        filename=filename,
        disable_notification=True,
    )
    except:
        return info

    populated_message = any(not item['inline_message_id'] for item in intents[query]['items'])
    await post_process(query, info, message, remove_message=not populated_message)

    for intent in intents[query]['items']:
        inline_message_id = intent.get('inline_message_id')
        if inline_message_id:
            await bot.edit_message_media(
            media=InputMediaVideo(
                media=message.video,
                width=width,
                height=height,
                duration=duration,
                thumbnail=thumbnail,
                caption=caption,
                filename=filename,
            ),
            inline_message_id=inline_message_id,
        )
    del intents[query]
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

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.removeprefix('/subscribe').lstrip():
        return await subscribe_url(update, context)
    else:
        await update.message.reply_text("Enter url")
        return SUBSCRIBE_URL

async def subscribe_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = message.from_user
    query = message.text.removeprefix('/subscribe').lstrip()
    
    print(f"{extract_user(user)} # subscribe_url: {query}")

    if not query: return ConversationHandler.END

    try:
        info = ydl.extract_info(query, download=False)
        uploader_url = info.get('uploader_url')
        if not uploader_url:
            await update.message.reply_text("Unsupported url", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        playlists = f"{uploader_url}/playlists"
        if query != playlists:
            info = ydl.extract_info(playlists, download=False)
    except:
        print(f"{extract_user(user)} # subscribe_url_failed: {query}")
        await update.message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    entries = info.get('entries')
    uploader = info.get('uploader') or info.get('uploader_id')
    uploader_videos = f"{uploader_url}/videos"
    if not entries or not uploader or not uploader_videos: return ConversationHandler.END

    urls = { uploader: uploader_videos }
    for item in entries:
        urls[item['title']] = item['url']

    context.user_data['urls'] = urls

    await update.message.reply_text("Select playlist", reply_markup=ReplyKeyboardMarkup(
        [[button] for button in list(urls.keys())], one_time_keyboard=True, input_field_placeholder="Select playlist", resize_keyboard=True
    ))
    return SUBSCRIBE_PLAYLIST
    
async def subscribe_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = message.from_user
    chat_id = message.chat_id
    query = message.text

    urls = context.user_data.pop('urls')
    if urls:
        url = urls.get(query)
    else:
        url = query

    print(f"{extract_user(user)} # subscribe_playlist: {query} - {url}")

    if not url:
        await update.message.reply_text("Invalid selection", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    users[chat_id] = user.to_dict()
    subscription = subscriptions.get(url)
    if subscription:
        chat_ids = subscription['chat_ids']
        if chat_id in chat_ids:
            await update.message.reply_text("Already subscribed", reply_markup=ReplyKeyboardRemove())
        else:
            chat_ids.append(chat_id)
            await update.message.reply_text("Subscribed", reply_markup=ReplyKeyboardRemove())

        return ConversationHandler.END
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
            await update.message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        
    subscriptions[url] = {
        'chat_ids': [chat_id],
        'title': title,
        'uploader': uploader,
        'uploader_videos': uploader_videos,
    }
    await update.message.reply_text("Subscribed", reply_markup=ReplyKeyboardRemove())

    return ConversationHandler.END

async def cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    print(f"{extract_user(update.message.from_user)} # cancel")
    await update.message.reply_text("Cancelled", reply_markup=ReplyKeyboardRemove())
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

    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler(['das', 'dv'], das_command))

    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(ChosenInlineResultHandler(chosen_query))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['subscribe'], subscribe)],
        states={
            SUBSCRIBE_URL: [MessageHandler(filters.TEXT, subscribe_url)],
            SUBSCRIBE_PLAYLIST: [MessageHandler(filters.TEXT, subscribe_playlist)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    asyncio.get_event_loop().run_until_complete(populate_animation(bot))
    Thread(target=populate_channels, daemon=True).start()
    Thread(target=populate_files, daemon=True).start()
    Thread(target=process_intents, args=(bot,), daemon=True).start()
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
