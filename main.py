import os, time, json, asyncio, yt_dlp
from threading import Thread, Condition
from dotenv import load_dotenv
from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, Bot, \
    InlineQueryResultCachedVideo, User, Message
from telegram.ext import Application, CommandHandler, ContextTypes, InlineQueryHandler, ChosenInlineResultHandler

load_dotenv()

developer_chat_id = os.getenv('DEVELOPER_CHAT_ID')
animation_file_id: str

ydl_opts = {
    'format': 'mp4',
    'outtmpl': 'videos/%(upload_date)s - %(title).80s [%(id)s].%(ext)s',
    'noplaylist': True,
    'extract_flat': True,
    'playlist_items': '1-20',
    'quiet': True,
    'progress': True,
}
ydl = yt_dlp.YoutubeDL(ydl_opts)

video_info_file = "config/videos.json"
populate_channels_file = "config/download.txt"
videos = {}
intents = {}

populate_channels_interval_sec = 60 * 60 # an hour
download_video_condition = Condition()

def write_video_info_file():
    try:
        file = open(video_info_file, "w", encoding='utf8')
        json.dump(videos, file, indent=1, ensure_ascii=False)
        file.write('\r')
    except:
        pass

def read_video_info_file() -> dict:
    global videos
    try:
        with open(video_info_file, "r", encoding='utf8') as file:
            obj = json.load(file)
            videos = obj
            return obj
    except:
        write_video_info_file()
        return {}

def populate_video_info_file():
    interval_sec = 60 * 10 # 10 mins
    while True:
        time.sleep(interval_sec)
        write_video_info_file()

read_video_info_file()

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

def extract_url(info: dict) -> str:
    return info.get('webpage_url') or info['url']

def now() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')

def extract_user(user: User) -> str:
    return f"{now()} {user.username} ({user.id})"

def extract_info(query: str, download=True) -> dict:
    info = videos.get(query)
    if info and (info.get('file_id') or not download):
        videos[query]['requested'] = now()
        return info
    
    if not info:
        info = ydl.extract_info(query, download=False)
        url = extract_url(info)
        info_url = videos.get(url)
        if info_url:
            info_url['requested'] = now()
            videos[url] = info_url
            videos[query] = info_url
            return info_url

    if not info.get('file_id') and download:
        try:
            info = ydl.extract_info(query)
        except:
            pass
    return process_info(info)

async def post_process(query: str, info: dict, message: Message, remove_message=True, store_info=True) -> str:
    file_id = message.video.file_id
    filepath = info['filepath']
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
    os.remove(filepath)
    return file_id

def process_info(info: dict) -> dict:
    filepath = None
    filename = None
    requested_downloads_list = info.get('requested_downloads')
    if requested_downloads_list:
        requested_downloads = requested_downloads_list[0]
        filepath = requested_downloads['filepath']
        filename = requested_downloads['filename']
    return {
        'file_id': info.get('file_id'),
        'webpage_url': info.get('webpage_url'),
        'title': info.get('title'),
        'description': info.get('description'),
        'thumbnail': info.get('thumbnail'),
        'duration': int(info.get('duration') or 0),
        'uploader_url': info.get('uploader_url'),
        'width': info.get('width'),
        'height': info.get('height'),
        'caption': f"{info['title']}\n{extract_url(info)}",
        'created': now(),
        'requested': now(),
        'url': info.get('url'),
        'filepath': filepath,
        'filename': filename,
        'entries': info.get('entries'),
    }

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
    url = extract_url(info)
    return InlineQueryResultCachedVideo(
        id=url,
        video_file_id=info.get('file_id') or animation_file_id,
        title=info['title'],
        description=info['description'],
        caption=info.get('caption'),
        reply_markup=InlineKeyboardMarkup(
            [[ InlineKeyboardButton(text='loading', url=url) ]]
        )
    )

async def start_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    username = update.message.from_user['username']
    await update.message.reply_text(f"Hey, @{username}.\n"
                                    "Welcome to Download and Share Online Video bot\n"
                                    "Type @dasovbot <video url>\n"
                                    "or /das <video url>\n"
                                    "/help - for more details")

async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "@dasovbot <video url> - download and send video\n"
        "/das <video url> - download video"
    )

async def das_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = message.from_user
    query = message.text.removeprefix('/das').removeprefix('/dv').lstrip()

    if not query:
        await update.message.reply_text('Type /das <video url>')
        return

    info = extract_info(query)
    entries = info.get('entries')
    if entries: return

    video = info.get('file_id') or info['filepath']
    filename = info['filename']

    message = await update.message.reply_video(
        video=video,
        filename=filename,
        duration=info['duration'],
        caption=info.get('caption'),
        width=info.get("width"),
        height=info.get("height"),
        reply_to_message_id=update.message.id,
    )

    await post_process(query, info, message, remove_message=False)

    print(f"{extract_user(user)} # das: {query}")

async def inline_query(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query = update.inline_query
    user = inline_query.from_user
    query = inline_query.query.lstrip()

    if not query: return

    info = extract_info(query, download=False)

    file_id = info.get('file_id')
    entries = info.get('entries')

    if entries:
        results = [inline_video(item) for item in extract_nested_entries(entries)]
    elif file_id:
        results = [
            InlineQueryResultCachedVideo(
                id=extract_url(info),
                video_file_id=file_id,
                title=info['title'],
                description=info['description'],
                caption=info.get('caption'),
            )
        ]
    else:
        results = [inline_video(info)]

    print(f"{extract_user(user)} # inline_query: {query}")
    await update.inline_query.answer(
        results=results,
        cache_time=10,
    )

async def chosen_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_result = update.chosen_inline_result
    inline_message_id = inline_result.inline_message_id
    
    if not inline_message_id: return

    query = inline_result.result_id
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

    duration = info['duration']
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

def main() -> None:
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

    asyncio.get_event_loop().run_until_complete(populate_animation(bot))
    Thread(target=populate_channels, daemon=True).start()
    Thread(target=populate_video_info_file, daemon=True).start()
    Thread(target=process_intents, args=(bot,), daemon=True).start()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
