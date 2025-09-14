import os, shutil, traceback, re, dotenv, asyncio, ffmpeg, yt_dlp
from yt_dlp import DownloadError
from threading import Lock
from contextlib import asynccontextmanager
from utils import ydl_opts, extract_url, now, process_info, write_file, read_file, video_info_file, user_info_file, subscription_info_file, intent_info_file, timestamp_file, remove, empty_media_folder_files
from constants import VIDEO_ERROR_MESSAGES, INTERVAL_SEC, TIMEOUT_SEC
from uuid import uuid4
from warnings import filterwarnings
from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, Bot, InlineQueryResultCachedVideo, User, Message
from telegram.ext import filters, Application, CommandHandler, MessageHandler, ContextTypes, InlineQueryHandler, ChosenInlineResultHandler, ConversationHandler, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.warnings import PTBUserWarning
from telegram.error import NetworkError
import logging

SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW, = range(3)
UNSUBSCRIBE_PLAYLIST, = range(1)
MULTIPLE_SUBSCRIBE_URLS = range(1)
DAS_URL, = range(1)

dotenv.load_dotenv()

developer_chat_id = os.getenv('DEVELOPER_CHAT_ID')
developer_id = os.getenv('DEVELOPER_ID') or developer_chat_id
animation_file_id: str

videos = {}
users = {}
subscriptions = {}
intents = {}
temporary_inline_queries = {}

download_video_condition = asyncio.Queue()

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

loop = asyncio.new_event_loop()
lock = Lock()

ydl = yt_dlp.YoutubeDL(ydl_opts)

logger = logging.getLogger(__name__)

async def populate_files():
    while True:
        await asyncio.sleep(INTERVAL_SEC)
        write_file(video_info_file, videos)
        write_file(user_info_file, users)
        write_file(subscription_info_file, subscriptions)
        write_file(intent_info_file, intents)
        with open(timestamp_file, 'w') as f: f.write(now())

videos = read_file(video_info_file, videos)
users = read_file(user_info_file, users)
subscriptions = read_file(subscription_info_file, subscriptions)
intents = read_file(intent_info_file, intents)

def remove_command_prefix(command: str) -> str:
    return re.sub(r'^/\w+', '', command).lstrip()

def process_entries(entries: list) -> list:
    nested_entries = entries[0].get('entries')
    return nested_entries if nested_entries else filter_entries(entries)

def filter_entries(entries: list) -> list:
    return list(filter(lambda entry: entry.get('duration') and (entry.get('live_status') is None or entry['live_status'] != 'is_live') and (entry.get('availability') is None or entry['availability'] != 'subscriber_only'), entries))

def filter_intents(intents: dict) -> dict:
    return {query: intent for query, intent in intents.items() if not intent['ignored']}
    
def contains_text(origin: str, text: list[str]) -> bool:
    for item in text:
        if origin.lower().__contains__(item.lower()): return True
    return False

def extract_user(user: User) -> str:
    return f"{now()} {user.username} ({user.id})"

def user_subscriptions(chat_id: str) -> dict:
    user_subscriptions = {}

    for url, subscription in subscriptions.copy().items():
        if subscription['chat_ids'].__contains__(str(chat_id)):
            user_subscriptions[str(uuid4())] = { 'title': subscription['title'], 'url': url }

    return user_subscriptions

def append_playlist(playlists, title, url):
    id = str(uuid4())
    playlists[id] = { 'title': title, 'url': url }

async def extract_info(query: str, download: bool) -> dict:
    info = videos.get(query)
    if info and (info.get('file_id') or not download):
        return info
    
    if not info:
        try:
            info = ydl.extract_info(query, download=False)
            url = extract_url(info)
            info_url = videos.get(url)
            if info_url:
                videos[query] = info_url
                return info_url
        except Exception as e:
            if isinstance(e, DownloadError):
                if contains_text(e.msg, VIDEO_ERROR_MESSAGES):
                    intent = intents.get(query)
                    if not intent:
                        intent = temporary_inline_queries.get(query)
                    if intent:
                        intent.update({'ignored': True})
                    return
            logger.error(f"{now()} # extract_info error: {query}")

    if (not info or not info.get('file_id')) and download:
        try:
            async with download_video_lock():
                future = loop.run_in_executor(None, ydl.extract_info, query)
                info = await asyncio.wait_for(future, TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.error(f"{now()} # extract_info timeout: {query}")
        except Exception as e:
            logger.error(f"{now()} # extract_info download error: {query}")
            traceback.print_exception(e)
    return process_info(info)

async def post_process(query: str, info: dict, message: Message, store_info=True) -> str:
    file_id = message.video.file_id
    try: await message.delete()
    except: pass
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
    if filepath:
        chat_ids = []
        intent = intents.get(query)
        if intent: chat_ids = intent['chat_ids'] or [message['chat'] for message in intent['messages']]
        if chat_ids.__contains__(developer_id) or str(message.chat_id) == developer_id:
            try: shutil.move(filepath, '/export/'.join(filepath.rsplit('/media/', 1)))
            except: 
                logger.error(f"{now()} # move_file error: {query}")
                remove(filepath)
        else: remove(filepath)
    return file_id

async def append_intent(query: str, chat_ids: list = [], inline_message_id: str = '', message: dict = {}):
    intent = intents.setdefault(query, {
        'chat_ids': [],
        'inline_message_ids': [],
        'messages': [],
        'priority': 0,
        'ignored': False,
    })

    intent_chat_ids = intent['chat_ids']
    intent_inline_message_ids = intent['inline_message_ids']
    intent_messages = intent['messages']
    
    for item in chat_ids:
        if item not in intent_chat_ids:
            intent_chat_ids.append(item)
    if inline_message_id: intent_inline_message_ids.append(inline_message_id)
    if message: intent_messages.append(message)
    if not intent['ignored']: intent['priority'] += len(chat_ids) or 2
    download_video_condition.put_nowait(query)

async def clear_temporary_inline_queries():
    while True:
        for url in temporary_inline_queries.copy():
            if temporary_inline_queries[url]['marked']: del temporary_inline_queries[url]
            else: temporary_inline_queries[url]['marked'] = True
        await asyncio.sleep(10 * 60)

async def process_intents(bot: Bot):
    while True:
        await asyncio.sleep(10)
        filtered_intents = filter_intents(intents)
        if not filtered_intents: await download_video_condition.get()
        if not filtered_intents: continue
        max_priority = max(filtered_intents, key=lambda key: filtered_intents[key]['priority'])
        await process_query(bot, max_priority)

async def monitor_process_intents(bot: Bot):
    empty_media_folder = os.getenv('EMPTY_MEDIA_FOLDER', 'false').lower() == 'true'
    while True:
        try: await process_intents(bot)
        except Exception as e:
            logger.error(f"{now()} # process_intents crashed: {type(e).__name__}, {str(e)}")
            traceback.print_exception(e)
            if empty_media_folder: empty_media_folder_files()
        await asyncio.sleep(INTERVAL_SEC)
        await send_message_developer(bot, 'monitor_process_intents')

async def populate_subscriptions():
    while True:
        for url in list(subscriptions.keys()):
            chat_ids = subscriptions[url]['chat_ids']
            if chat_ids: await populate_playlist(url, chat_ids)
            else: subscriptions.pop(url, None)
        await asyncio.sleep(INTERVAL_SEC)

async def populate_playlist(channel: str, chat_ids: list):
    try:
        info = ydl.extract_info(channel, download=False)
    except:
        logger.error(f"{now()} # populate_playlist error: {channel}")
        return
    entries = info.get('entries')
    if not entries:
        logger.warning(f"{now()} # populate_playlist no entries: {channel}")
        return
    for entry in filter_entries(entries)[:5][::-1]: await populate_video(extract_url(entry), chat_ids)

async def populate_video(query: str, chat_ids: list):
    info = videos.get(query)
    file_id = info.get('file_id') if info else None
    if file_id: return info
    await append_intent(query, chat_ids = chat_ids)

def inline_video(info, inline_queries) -> InlineQueryResultCachedVideo:
    id = str(uuid4())
    url = extract_url(info)
    file_id = info.get('file_id')
    video_file_id = file_id or animation_file_id
    reply_markup = InlineKeyboardMarkup([[ InlineKeyboardButton(text='loading', url=url) ]]) if not file_id else None
    inline_queries[id] = url

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
                                    "Type /download\n\n"
                                    "/help - for more details")

async def help(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_markdown(
        "@dasovbot - Download and share video\n\n"
        "/download - Download video\n\n"
        "*Subscriptions*\n"
        "/subscriptions - Show list of subscriptions\n"
        "/subscribe - Subscribe to playlist\n"
        "/unsubscribe - Unsubscribe from playlist"
    )

async def unknown(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Unknown command. Please type /help for available commands"
    )

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inline_query = update.inline_query
    user = inline_query.from_user
    query = inline_query.query.lstrip()

    logger.debug(f"{extract_user(user)} # inline_query: {query}")

    if not query: return

    temporary_inline_query = temporary_inline_queries.setdefault(query, {
        'timestamp': now(),
        'results': [],
        'inline_queries': {},
        'marked': False,
        'ignored': False,
    })

    if temporary_inline_query['ignored']:
        logger.warning(f"{now()} # inline_query ignored: {query}")
        try: await inline_query.answer(results=[])
        except: pass
        return

    results = temporary_inline_query['results']
    info = videos.get(query)
    if results and not info:
        context.user_data['inline_queries'] = temporary_inline_query['inline_queries']
        try: await inline_query.answer(results=results, cache_time=1)
        except: pass
        return

    info = await extract_info(query, download=False)
    if not info:
        logger.warning(f"{now()} # inline_query no info: {query}")
        try: await inline_query.answer(results=[])
        except: pass
        return

    entries = info.get('entries')
    inline_queries = {}

    if entries:
        results = [inline_video(process_info(item), inline_queries) for item in process_entries(entries)]
    else:
        results = [inline_video(info, inline_queries)]

    temporary_inline_query['results'] = results
    temporary_inline_query['inline_queries'] = inline_queries

    context.user_data['inline_queries'] = inline_queries

    if not results: logger.warning(f"{now()} # inline_query no results: {query}")

    try:
        await inline_query.answer(results=results, cache_time=1)
    except Exception as e:
        single_video = len(results) == 1
        traceback.print_exception(e)
        logger.error(f"{now()} # inline_query answer error: {query}, single: {single_video}, {type(e)=}, {e=}")
        if (single_video): await populate_video(query, chat_ids = [user.id])

async def chosen_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inline_result = update.chosen_inline_result
    inline_message_id = inline_result.inline_message_id
    inline_queries = context.user_data.pop('inline_queries', None)

    if not inline_message_id or not inline_queries: return
    query = inline_queries[inline_result.result_id]
    if not query: return
    user = inline_result.from_user

    logger.debug(f"{extract_user(user)} # chosen_query strt: {query}")

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
        logger.debug(f"{extract_user(user)} # chosen_query fnsh: {query}")
        return
    
    await append_intent(query, inline_message_id = inline_message_id)
    logger.debug(f"{extract_user(user)} # chosen_query aint: {query}")

async def process_query(bot: Bot, query: str) -> dict:
    info = await extract_info(query, download=True)
    if not info:
        logger.warning(f"{now()} # process_query error: {query}")
        if intents.get(query) and not intents[query]['ignored']: intents.pop(query, None)
        return info
    caption = info.get('caption')
    file_id = info.get('file_id')
    if not file_id:
        try:
            video_path = info['filepath']
            logger.debug(f"{now()} # process_query send_video strt: {query}")
            message = await bot.send_video(
                chat_id=developer_chat_id,
                caption=caption,
                video=video_path,
                duration=info.get('duration'),
                width=info.get('width'),
                height=info.get('height'),
                filename=info['filename'],
                disable_notification=True,
            )
            logger.debug(f"{now()} # process_query send_video fnsh: {query}")
        except Exception as e:
            if isinstance(e, NetworkError) and video_path and os.path.getsize(video_path) >> 20 > 2000 and 'youtube' in extract_url(info):
                await send_message_developer(bot, f'[large_video_error]\n{caption}')
                base, ext = os.path.splitext(video_path)
                temp_video_path = f'{base}.temp{ext}'
                ffmpeg.input(video_path).filter('scale', -1, 360).output(temp_video_path, format='mp4', map='0:a:0', loglevel='quiet').run()
                try:
                    logger.debug(f"{now()} # process_query send_video rsrt: {query}")
                    message = await bot.send_video(
                        chat_id=developer_chat_id,
                        caption=caption,
                        video=temp_video_path,
                        duration=info.get('duration'),
                        width=info.get('width'),
                        height=info.get('height'),
                        filename=info['filename'],
                        disable_notification=True,
                    )
                    logger.debug(f"{now()} # process_query send_video fnsh: {query}")
                    await send_message_developer(bot, f'[large_video_fixed]\n{caption}', notification=False)
                    file_id = await post_process(query, info, message)
                    await process_intent(bot, query, file_id, caption)
                    return info
                except: pass
                finally: remove(temp_video_path)
            intents.pop(query, None)
            return info
        file_id = await post_process(query, info, message)
    
    await process_intent(bot, query, file_id, caption)
    return info

async def process_intent(bot: Bot, query: str, video: str, caption: str) -> dict:
    intent = intents.pop(query, None)
    for item in intent['chat_ids']:
        try: await bot.send_video(chat_id=item, video=video, caption=caption, disable_notification=True)
        except: logger.error(f"{now()} # process_intent chat_ids error: {query} - {item}")
    for item in intent['inline_message_ids']:
        try: await bot.edit_message_media(inline_message_id=item, media=InputMediaVideo(media=video, caption=caption))
        except: logger.error(f"{now()} # process_intent inline_message_ids error: {query} - {item}")
    for item in intent['messages']:
        try: await bot.edit_message_media(chat_id=item['chat'], message_id=item['message'], media=InputMediaVideo(media=video, caption=caption))
        except: logger.error(f"{now()} # process_intent messages error: {query} - {item}")
    return intent

async def populate_animation(bot: Bot):
    global animation_file_id
    animation_file_id = os.getenv('ANIMATION_FILE_ID')
    if animation_file_id:
        logger.warning(f"{now()} # saved_animation_file_id = {animation_file_id}")
        return

    query = os.getenv('LOADING_VIDEO_ID')

    info = await extract_info(query, download=True)

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

    animation_file_id = await post_process(query, info, message, store_info=False)
    logger.warning(f"{now()} # animation_file_id = {animation_file_id}")

async def subscription_list(update: Update, _: ContextTypes.DEFAULT_TYPE):
    message = update.message
    subscription_list = [f"[{item['title'].replace('[','').replace(']','')}]({item['url']})" for item in user_subscriptions(message.chat_id).values()]

    try:
        if subscription_list: await message.reply_markdown('\n\n'.join(subscription_list))
        else: await message.reply_text('No subscriptions')
    except: pass

async def download(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if remove_command_prefix(message.text):
        return await download_url(update, _)
    else:
        await message.reply_text("Enter url")
        return DAS_URL
    
async def download_url(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = message.from_user
    chat_id = str(message.chat_id)
    query = remove_command_prefix(message.text)

    logger.debug(f"{extract_user(user)} # download_url: {query}")

    if not query: return ConversationHandler.END

    info = await extract_info(query, download=False)
    if not info or info.get('entries'):
        await message.reply_text("Unsupported url", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    try:
        video = await message.reply_video(
            video=animation_file_id,
            caption=info.get('caption'),
            reply_to_message_id=message.id,
        )
        await append_intent(query, message = { 'chat':chat_id, 'message':str(video.message_id) })
    except Exception as e:
        traceback.print_exception(e)
        logger.error(f"{extract_user(user)} # download_url error: {query}, {type(e)=}, {e=}")

    users[chat_id] = user.to_dict()
    return ConversationHandler.END

async def multiple_subscribe(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Enter urls")
    return MULTIPLE_SUBSCRIBE_URLS

async def multiple_subscribe_urls(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = message.from_user
    query = message.text
    chat_id = str(message.chat_id)

    if not query: return ConversationHandler.END

    urls = query.strip('\n').split('\n')
    subscribed = []
    already_subscribed = []
    failed = []

    logger.debug(f"{extract_user(user)} # multiple_subscribe: {len(urls)} urls")

    for url in urls:
        url = url.strip()
        if not url:
            failed.append(url)
            continue
        subscription = subscriptions.get(url)
        if subscription:
            chat_ids = subscription['chat_ids']
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
            subscriptions[url] = {
                'chat_ids': [chat_id],
                'title': title,
                'uploader': uploader,
                'uploader_videos': url,
            }
            subscribed.append(url)
    if already_subscribed: await message.reply_text('\n'.join(["Already Subscribed"] + [f"{item}" for item in already_subscribed]))
    if failed: await message.reply_text('\n'.join(["Failed subscriptions"] + [f"{item}" for item in failed]))
    logger.debug(f"{extract_user(user)} # multiple_subscribe len: {len(urls)}, already_subscribed: {len(already_subscribed)}, failed: {len(failed)}")
    await message.reply_text(f"Multiple Subscribe" + (f"\n{len(subscribed)} urls successfully" if subscribed else "") + (f"\n{len(failed)} urls failed" if failed else "") + (f"\n{len(already_subscribed)} urls already subscribed" if already_subscribed else ""))
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
    
    logger.debug(f"{extract_user(user)} # subscribe_url: {query}")

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
            context.user_data['uploader_videos'] = f"{uploader_url}/videos"
            return await subscribe_playlist(update, context)

    except:
        logger.error(f"{extract_user(user)} # subscribe_url failed: {query}")
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
        info = ydl.extract_info(uploader_streams, download=False)
        append_playlist(playlists, f"{uploader} Streams", uploader_streams)
    except: pass

    for entry in entries:
        url = extract_url(entry)
        append_playlist(playlists, entry['title'], url)
        if query == url: return await subscribe_playlist(update, context)

    context.user_data['playlists'] = playlists
    await message.reply_markdown(f"Select playlist of [{uploader}]({uploader_url})", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=item['title'], callback_data=id)] for id, item in playlists.items()]
        + [[InlineKeyboardButton(text='Cancel', callback_data='cancel')]]
    ))
    return SUBSCRIBE_PLAYLIST
    
async def subscribe_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    playlists = context.user_data.pop('playlists', None)
    uploader_videos = context.user_data.pop('uploader_videos', None)
    callback_query = update.callback_query

    if callback_query:
        await callback_query.answer()
        message = callback_query.message
        callback_data = callback_query.data
        if callback_data == 'cancel':
            try: await message.delete()
            except: pass
            return ConversationHandler.END
        user = callback_query.from_user
        message_text = message.edit_text

        if not playlists:
            logger.warning(f"{extract_user(user)} # subscribe_playlist failed")
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
        if uploader_videos: url = uploader_videos
        else: url = remove_command_prefix(message.text)

    logger.debug(f"{extract_user(user)} # subscribe_playlist: {title} - {url}")

    if not url:
        await message_text("Invalid selection", reply_markup=InlineKeyboardMarkup([]))
        return ConversationHandler.END

    chat_id = str(message.chat_id)
    users[chat_id] = user.to_dict()
    subscription = subscriptions.get(url)
    reply_markup = InlineKeyboardMarkup(
        [[ InlineKeyboardButton(text='Yes', callback_data='True'), InlineKeyboardButton(text='No', callback_data='False') ]]
    )
    if subscription:
        chat_ids = subscription['chat_ids']
        subscription_info = f"[{subscription['title']}]({url})"
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
            logger.error(f"{extract_user(user)} # subscribe_playlist failed: {url}")
            await message_text("Error occured", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END

    subscriptions[url] = {
        'chat_ids': [chat_id],
        'title': title,
        'uploader': uploader,
        'uploader_videos': uploader_videos,
    }
    subscription_info = f"[{title}]({url})"
    context.user_data['subscription_url'] = url
    await message_text(f"Subscribed to {subscription_info}\nShow latest videos?", parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    return SUBSCRIBE_SHOW

async def subscribe_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            info = ydl.extract_info(subscription_url, download=False)
            entries = info.get('entries')
            for entry in entries[:5]:
                video = videos.get(extract_url(entry))
                file_id = video.get('file_id') if video else None
                if file_id: await context.bot.send_video(chat_id, file_id, caption=video.get('caption'))
        except: pass
    return ConversationHandler.END

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if remove_command_prefix(message.text):
        return await unsubscribe_playlist(update, context)
    else:
        subscriptions = user_subscriptions(message.chat_id)
        if subscriptions:
            context.user_data['user_subscriptions'] = subscriptions
            await message.reply_text("Select playlist", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(text=item['title'], callback_data=id)] for id, item in subscriptions.items()]
                + [[InlineKeyboardButton(text='Cancel', callback_data='cancel')]]
            ))
            return UNSUBSCRIBE_PLAYLIST
        else:
            await message.reply_text("No subscription found")
            return ConversationHandler.END

async def unsubscribe_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback_query = update.callback_query
    if callback_query:
        await callback_query.answer()
        message = callback_query.message
        callback_data = callback_query.data
        if callback_data == 'cancel':
            try: await message.delete()
            except: pass
            return ConversationHandler.END
        user = callback_query.from_user
        message_text = message.edit_text
        user_subscriptions = context.user_data.pop('user_subscriptions', None)

        if not user_subscriptions:
            logger.warning(f"{extract_user(user)} # unsubscribe_playlist failed")
            await message_text("Error occured", reply_markup=InlineKeyboardMarkup([]))
            return ConversationHandler.END

        query = user_subscriptions[callback_data]['url']
    else:
        message = update.message
        user = message.from_user
        message_text = message.reply_text
        query = remove_command_prefix(message.text)
    
    chat_id = str(message.chat_id)
    subscription = subscriptions.get(query)

    logger.debug(f"{extract_user(user)} # unsubscribe_playlist: {query}")

    if not subscription:
        await message_text("Invalid selection", reply_markup=InlineKeyboardMarkup([]))
        return ConversationHandler.END
    
    chat_ids = subscription['chat_ids']
    if chat_id not in chat_ids:
        await message_text("No subscription found", reply_markup=InlineKeyboardMarkup([]))
        return ConversationHandler.END

    chat_ids[:] = (item for item in chat_ids if item != chat_id)
    if not chat_ids: subscriptions.pop(query, None)

    subscription_info = f"[{subscription['title']}]({query})"
    await message_text(f"Unsubscribed from {subscription_info}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([]))
    return ConversationHandler.END

async def playlists(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    subscription_list = [item['url'] for item in user_subscriptions(message.chat_id).values()]

    try:
        if not subscription_list:
            await message.reply_text('No subscriptions')
            return ConversationHandler.END
    except: pass

    videos = []
    streams = []
    both = []
    already_processed = []
    for subscription in subscription_list:
        if subscription in already_processed: continue
        try:
            info = ydl.extract_info(subscription, download=False)
            uploader_url = info.get('uploader_url')
            if not uploader_url: continue
            uploader_videos = f"{uploader_url}/videos"
            uploader_streams = f"{uploader_url}/streams"
            subscription_videos = uploader_url in subscription_list or uploader_videos in subscription_list
            subscription_streams = uploader_streams in subscription_list
            if subscription_videos and subscription_streams:
                both.append(uploader_url)
                already_processed.append(uploader_url)
                already_processed.append(uploader_videos)
                already_processed.append(uploader_streams)
            elif subscription_videos:
                try:
                    info = ydl.extract_info(uploader_streams, download=False)
                    streams.append(uploader_streams)
                except: pass
            elif subscription_streams:
                try:
                    info = ydl.extract_info(uploader_videos, download=False)
                    videos.append(uploader_videos)
                except: pass
        except: pass
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

async def cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    logger.debug(f"{extract_user(message.from_user)} # cancel")
    await message.reply_text("Cancelled", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def send_message_developer(bot: Bot, text: str, notification: bool = True):
    try: await bot.send_message(chat_id=developer_id, text=text, disable_notification=not notification)
    except: pass

def main():
    asyncio.set_event_loop(loop)
    token = os.getenv('BOT_TOKEN')
    base_url = os.getenv('BASE_URL')
    timeout = os.getenv('READ_TIMEOUT') or 30

    application = (
        Application.builder()
        .token(token)
        .base_url(base_url)
        .read_timeout(float(timeout))
        .build()
    )
    bot = application.bot

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help))

    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(ChosenInlineResultHandler(chosen_query))
    application.add_handler(CommandHandler(['subscriptions', 'subs'], subscription_list))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['download', 'das', 'dv'], download)],
        states={
            DAS_URL: [MessageHandler(filters.TEXT, download_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['subscribe'], subscribe)],
        states={
            SUBSCRIBE_URL: [MessageHandler(filters.TEXT, subscribe_url)],
            SUBSCRIBE_PLAYLIST: [CallbackQueryHandler(subscribe_playlist)],
            SUBSCRIBE_SHOW: [CallbackQueryHandler(subscribe_show)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['unsubscribe'], unsubscribe)],
        states={
            UNSUBSCRIBE_PLAYLIST: [CallbackQueryHandler(unsubscribe_playlist)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(CommandHandler(['playlists'], playlists))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler(['multiple_subscribe'], multiple_subscribe)],
        states={
            MULTIPLE_SUBSCRIBE_URLS: [MessageHandler(filters.TEXT, multiple_subscribe_urls)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    application.add_handler(CommandHandler(['multiple_subscribe'], multiple_subscribe))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    asyncio.gather(
        populate_animation(bot),
        populate_subscriptions(),
        populate_files(),
        monitor_process_intents(bot),
        clear_temporary_inline_queries(),
    )

    application.run_polling(allowed_updates=Update.ALL_TYPES)

@asynccontextmanager
async def download_video_lock():
    try:
        logger.debug(f"{now()} # lock_acquire")
        lock.acquire()
        yield
    finally:
        logger.debug(f"{now()} # lock_release")
        lock.release()

if __name__ == "__main__":
    main()
