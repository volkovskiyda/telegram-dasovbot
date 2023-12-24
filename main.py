import asyncio
import os
import datetime

import yt_dlp
from dotenv import load_dotenv
from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, Bot, \
    InlineQueryResultCachedVideo, User, Message
from telegram.ext import Application, CommandHandler, ContextTypes, InlineQueryHandler, ChosenInlineResultHandler

load_dotenv()

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

videos = {}

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
    return info

def extract_entries(entries):
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

def extract_caption(info: dict) -> str:
    return f"{info['title']}\n{extract_url(info)}"

def extract_duration(info: dict) -> int:
    return int(info['duration'])

def extract_user(user: User) -> str:
    return f"{datetime.datetime.now()} {user.username} ({user.id})"

async def post_process(query: str, info: dict, message: Message, remove_message=True, store_info=True) -> str:
    file_id = message.video.file_id
    filepath = info['requested_downloads'][0]['filepath']
    info.setdefault('file_id', file_id)
    url = extract_url(info)
    if store_info:
        videos[query] = info
        videos[url] = info
    if remove_message: await message.delete()
    os.remove(filepath)
    return file_id

def inline_video(info) -> InlineQueryResultCachedVideo:
    url = extract_url(info)
    return InlineQueryResultCachedVideo(
        id=url,
        video_file_id=info.get('file_id') or animation_file_id,
        title=info['title'],
        description=info['description'],
        caption=extract_caption(info),
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

    requested_downloads = info['requested_downloads'][0]
    video = info.get('file_id') or requested_downloads['filepath']
    filename = requested_downloads['filename']

    message = await update.message.reply_video(
        video=video,
        duration=extract_duration(info),
        caption=extract_caption(info),
        width=info.get("width"),
        height=info.get("height"),
        filename=filename,
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
        entries = extract_entries(entries)
        results = [inline_video(item) for item in entries]
    elif file_id:
        results = [
            InlineQueryResultCachedVideo(
                id=extract_url(info),
                video_file_id=file_id,
                title=info['title'],
                description=info['description'],
                caption=extract_caption(info),
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
                caption=extract_caption(info),
            ),
            inline_message_id=inline_message_id,
        )
        print(f"{extract_user(user)} # chosen_query_fnsh: {query}")
        return

    info = extract_info(query)

    duration = extract_duration(info)
    width = info.get('width')
    height = info.get('height')
    thumbnail = info['thumbnail']
    caption = extract_caption(info)
    requested_downloads = info['requested_downloads'][0]
    filepath = requested_downloads['filepath']
    filename = requested_downloads['filename']

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
    chat_id = os.getenv('DEVELOPER_CHAT_ID')

    info = ydl.extract_info(query)
    requested_downloads = info['requested_downloads'][0]
    filepath = requested_downloads['filepath']
    filename = requested_downloads['filename']

    message = await bot.send_video(
        chat_id=chat_id,
        video=filepath,
        duration=extract_duration(info),
        width=info['width'],
        height=info['height'],
        filename=filename,
        caption=info['title'],
        disable_notification=True,
    )

    global animation_file_id
    animation_file_id = await post_process(query, info, message, store_info=False)
    print('animation_file_id = ' + animation_file_id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    return


def main() -> None:
    token = os.getenv('BOT_TOKEN')
    base_url = os.getenv('BASE_URL')
    timeout = os.getenv('READ_TIMEOUT') or 30
    asyncio.get_event_loop().run_until_complete(populate_animation(Bot(token=token, base_url=base_url)))

    application = (
        Application.builder()
        .token(token)
        .base_url(base_url)
        .read_timeout(float(timeout))
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler(['das', 'dv'], das_command))

    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(ChosenInlineResultHandler(chosen_query))

    application.add_error_handler(error_handler)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
