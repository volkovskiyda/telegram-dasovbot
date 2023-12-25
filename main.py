import os, time, json, asyncio
from threading import Thread

import yt_dlp
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

video_info_file = "videos.json"
videos = {}

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
    if nested_entries:
        try:
            next_entries = entries[1].get('entries')
        except:
            next_entries = []
        entries = nested_entries + next_entries
    return entries

def extract_url(info: dict) -> str:
    return info.get('webpage_url') or info['url']

def extract_time() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')

def extract_user(user: User) -> str:
    return f"{extract_time()} {user.username} ({user.id})"

def extract_info(query: str, download=True) -> dict:
    info = videos.get(query)
    if info and (info.get('file_id') or not download): return info
    
    if not info:
        info = ydl.extract_info(query, download=False)
        url = extract_url(info)
        info_url = videos.get(url)
        if info_url:
            videos[query] = info_url
            return info_url

    if not info.get('file_id') and download:
        info = ydl.extract_info(query)
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
        'url': info.get('url'),
        'webpage_url': info.get('webpage_url'),
        'title': info.get('title'),
        'description': info.get('description'),
        'thumbnail': info.get('thumbnail'),
        'duration': int(info.get('duration') or 0),
        'uploader_url': info.get('uploader_url'),
        'width': info.get('width'),
        'height': info.get('height'),
        'filepath': filepath,
        'filename': filename,
        'entries': info.get('entries'),
        'caption': f"{info['title']}\n{extract_url(info)}",
    }

def populate_channels(bot: Bot):
    interval_min = os.getenv('POPULATE_CHANNELS_INTERVAL_MIN') or 60
    interval_sec = float(interval_min) * 60
    while True:
        try:
            with open("download.txt", "r") as file:
                channels = [line.rstrip() for line in file]
                start = time.time()
                for channel in channels:
                    asyncio.new_event_loop().run_until_complete(populate_channel(bot, channel))
                elapsed = time.time() - start
                if elapsed > 10 * len(channels): print(f"{extract_time()} # populate_channels {channels} took {elapsed}")
        except:
            pass
        finally:
            time.sleep(interval_sec)

async def populate_channel(bot: Bot, channel: str):
    info = ydl.extract_info(channel, download=False)
    entries = info.get('entries')
    uploader_url = info.get('uploader_url')
    if not entries:
        if not uploader_url: return
        info = ydl.extract_info(uploader_url, download=False)
        entries = info.get('entries')
    entries = extract_uploader_entries(entries)
    if not entries: return
    start = time.time()
    for entry in entries[:5]:
        await populate_video(bot, entry)
    elapsed = time.time() - start
    if elapsed > 5: print(f"{extract_time()} # populate_channel {channel} ({uploader_url}) took {elapsed}")

async def populate_video(bot: Bot, entry: dict) -> dict:
    query = extract_url(entry)
    info = videos.get(query)
    file_id = info.get('file_id') if info else None
    if file_id: return info
    start = time.time()
    try:
        info = extract_info(query)
        message = await bot.send_video(
            chat_id=developer_chat_id,
            video=info['filepath'],
            duration=info['duration'],
            width=info.get('width'),
            height=info.get('height'),
            caption=info.get('caption'),
            filename=info['filename'],
            disable_notification=True,
        )
        await post_process(query, info, message)
        return info
    except:
        pass
    finally:
        elapsed = time.time() - start
        file_id = info.get('file_id') if info else None
        if file_id:
            if elapsed > 1: print(f"{extract_time()} # populate_video {query} took {elapsed}: {file_id}")
        else:
            print(f"{extract_time()} # populate_video {entry.get('url')} failed after {elapsed}")

def extract_video_info():
    interval_sec = 60 * 10 # 10 mins
    while True:
        try:
            file = open(video_info_file, "w")
            json.dump(videos, file, indent=1, sort_keys=True)
            file.write('\r')
        except:
            pass
        finally:
            time.sleep(interval_sec)

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
    query = inline_query.query

    if not query: return

    info = extract_info(query, download=False)

    file_id = info.get('file_id')
    entries = info.get('entries')

    if entries:
        entries = extract_nested_entries(entries)
        results = [inline_video(item) for item in entries]
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
    chat_id = user.id

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
        print(f"{extract_user(user)} # chosen_query_fnsh: {query}")
        return

    info = extract_info(query)

    duration = info['duration']
    width = info.get('width')
    height = info.get('height')
    thumbnail = info['thumbnail']
    caption = info.get('caption')
    filepath = info['filepath']
    filename = info['filename']

    chat_id = inline_result.from_user.id
    message = await context.bot.send_video(
        chat_id=chat_id,
        video=filepath,
        duration=duration,
        width=width,
        height=height,
        caption=caption,
        filename=filename,
        disable_notification=True,
    )

    await post_process(query, info, message)

    await context.bot.edit_message_media(
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
    print(f"{extract_user(user)} # chosen_query_fnsh: {query}")

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
    print(f"{extract_time()} # animation_file_id = {animation_file_id}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    return

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

    application.add_error_handler(error_handler)

    asyncio.get_event_loop().run_until_complete(populate_animation(bot))
    Thread(target=populate_channels, args=(bot,), daemon=True).start()
    Thread(target=extract_video_info, daemon=True).start()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
