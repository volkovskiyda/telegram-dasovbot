import asyncio
import os
from typing import Optional
from uuid import uuid4

import yt_dlp
from dotenv import load_dotenv
from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, Bot, \
    InlineQueryResultCachedVideo
from telegram.ext import Application, CommandHandler, ContextTypes, InlineQueryHandler, ChosenInlineResultHandler

load_dotenv()

animation_file_id: str

ydl_opts = {
    'format': 'worst[height>=480]/mp4',
    'outtmpl': 'videos\%(upload_date)s - %(title)s [%(id)s].%(ext)s',
    'noplaylist': True,
    'extract_flat': True,
    'quiet': True,
}
ydl = yt_dlp.YoutubeDL(ydl_opts)

videos = {}


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
    query = update.message.text.removeprefix('/das').removeprefix('/dv').lstrip()

    if not query:
        await update.message.reply_text('Type /das <video url>')
        return

    if not videos.__contains__(query):
        # noinspection PyBroadException
        try:
            videos[query] = ydl.extract_info(query, download=True)
        except:
            return

    info = videos[query]

    requested_downloads = info['requested_downloads'][0]
    video = info.get('file_id') or requested_downloads['filepath']
    filename = requested_downloads['filename']

    message = await update.message.reply_video(
        video=video,
        duration=int(info['duration']),
        caption=f"{info['title']}\n{info.get('webpage_url') or info['url']}",
        width=info.get("width"),
        height=info.get("height"),
        thumbnail=info['thumbnail'],
        filename=filename,
        reply_to_message_id=update.message.id,
    )

    info.setdefault('file_id', message.video.file_id)


async def inline_query(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query.query

    if not query:
        return

    if not videos.__contains__(query):
        # noinspection PyBroadException
        try:
            info = ydl.extract_info(query, download=False)
        except:
            return
    else:
        info = videos[query]

    if videos.__contains__(query):
        file_id = info.get('file_id')
    else:
        file_id = None

    entries = info.get('entries')

    if entries:
        results = [inline_video(item, str(idx).zfill(2)) for idx, item in enumerate(entries[:10])]
    elif file_id:
        results = [
            InlineQueryResultCachedVideo(
                id=str(uuid4()),
                video_file_id=file_id,
                title=info['title'],
                description=info['description'],
                caption=f"{info['title']}\n{info.get('webpage_url') or info['url']}",
            )
        ]
    else:
        results = [inline_video(info)]

    await update.inline_query.answer(
        results=results,
        cache_time=10,
    )


def inline_video(info, playlist_index: Optional[str] = None) -> InlineQueryResultCachedVideo:
    return InlineQueryResultCachedVideo(
        id=str(uuid4()) + (playlist_index or ''),
        video_file_id=animation_file_id,
        title=info['title'],
        description=info['description'],
        caption=f"{info['title']}\n{info.get('webpage_url') or info['url']}",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    text='loading',
                    url=info.get('webpage_url') or info['url'],
                )
            ]]
        )
    )


async def chosen_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_result = update.chosen_inline_result
    query = inline_result.query
    inline_message_id = inline_result.inline_message_id

    if not inline_message_id:
        return

    if not videos.__contains__(query):
        # noinspection PyBroadException
        try:
            videos[query] = ydl.extract_info(query, download=True)
            entries = videos[query].get('entries')
            if entries:
                videos.pop(query)
                query = entries[int(inline_result.result_id[-2:])]['url']
                videos[query] = ydl.extract_info(query, download=True)
        except:
            return
    else:
        return

    info = videos[query]

    duration = int(info['duration'])
    width = info.get('width')
    height = info.get('height')
    thumbnail = info['thumbnail']
    caption = f"{info['title']}\n{info.get('webpage_url') or info['url']}"
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
        thumbnail=thumbnail,
        caption=caption,
        filename=filename,
        disable_notification=True,
    )
    await message.delete()
    os.remove(filepath)

    file_id = message.video.file_id
    info.setdefault('file_id', file_id)

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


async def populate_animation(bot: Bot):
    video_url = os.getenv('LOADING_VIDEO_ID')
    chat_id = os.getenv('DEVELOPER_CHAT_ID')

    animation_info = ydl.extract_info(video_url, download=True)
    requested_downloads = animation_info['requested_downloads'][0]
    filepath = requested_downloads['filepath']
    filename = requested_downloads['filename']

    message = await bot.send_video(
        chat_id=chat_id,
        video=filepath,
        duration=animation_info['duration'],
        width=animation_info['width'],
        height=animation_info['height'],
        thumbnail=animation_info['thumbnail'],
        filename=filename,
        caption=animation_info['title'],
        disable_notification=True,
    )
    await message.delete()
    os.remove(filepath)

    global animation_file_id
    animation_file_id = message.video.file_id
    print('animation_file_id = ' + animation_file_id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    return


def main() -> None:
    token = os.getenv('BOT_TOKEN')
    base_url = os.getenv('BASE_URL')
    asyncio.get_event_loop().run_until_complete(populate_animation(Bot(token=token, base_url=base_url)))

    application = (
        Application.builder()
        .token(token)
        .base_url(base_url)
        .read_timeout(60.0)
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
