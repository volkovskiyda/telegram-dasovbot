import os, shutil, re, json, asyncio, yt_dlp
from asyncio import Queue
from utils import ydl_opts, extract_url, now, process_info, config_folder
from uuid import uuid4
from dotenv import load_dotenv
from warnings import filterwarnings
from telegram import Update, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, Bot, InlineQueryResultCachedVideo, User, Message
from telegram.ext import filters, Application, CommandHandler, MessageHandler, ContextTypes, InlineQueryHandler, ChosenInlineResultHandler, ConversationHandler, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.warnings import PTBUserWarning

SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW, = range(3)
UNSUBSCRIBE_PLAYLIST, = range(1)
DAS_URL, = range(1)

load_dotenv()

developer_chat_id = os.getenv('DEVELOPER_CHAT_ID')
developer_id = os.getenv('DEVELOPER_ID') or developer_chat_id
animation_file_id: str

ydl = yt_dlp.YoutubeDL(ydl_opts)

video_info_file = f'{config_folder}/data/videos.json'
user_info_file = f'{config_folder}/data/users.json'
subscription_info_file = f'{config_folder}/data/subscriptions.json'
intent_info_file = f'{config_folder}/data/intents.json'

videos = {}
users = {}
subscriptions = {}
intents = {}

interval_sec = 60 * 60 # an hour
download_video_condition = Queue()

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

def write_file(file_path, dict):
    try:
        file = open(file_path, "w", encoding='utf8')
        json.dump(dict, file, indent=1, ensure_ascii=False)
        file.write('\r')
    except: pass

def read_file(file_path, dict) -> dict:
    try:
        with open(file_path, "r", encoding='utf8') as file:
            return json.load(file)
    except:
        write_file(file_path, dict)
        return {}

async def populate_files():
    while True:
        await asyncio.sleep(interval_sec)
        write_file(video_info_file, videos)
        write_file(user_info_file, users)
        write_file(subscription_info_file, subscriptions)
        write_file(intent_info_file, intents)

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
    return list(filter(lambda entry: entry.get('duration') and (entry.get('live_status') is None or entry['live_status'] != 'is_live'), entries))

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

def extract_info(query: str, download: bool) -> dict:
    info = videos.get(query)
    if info and (info.get('file_id') or not download): return info
    
    if not info:
        try:
            info = ydl.extract_info(query, download=False)
            url = extract_url(info)
            info_url = videos.get(url)
            if info_url:
                videos[query] = info_url
                return info_url
        except: print(f"{now()} # extract_info error: {query}")

    if (not info or not info.get('file_id')) and download:
        try: info = ydl.extract_info(query)
        except: pass
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
        intent = intents[query]
        if intent: chat_ids = intent['chat_ids'] or [message['chat'] for message in intent['messages']]
        if chat_ids.__contains__(developer_id) or str(message.chat_id) == developer_id:
            try: shutil.move(filepath, '/home/'.join(filepath.rsplit('/media/', 1)))
            except: remove(filepath)
        else: remove(filepath)
    return file_id

def remove(filepath: str):
    try: os.remove(filepath)
    except: pass

async def append_intent(query: str, chat_ids: list = [], inline_message_id: str = '', message: dict = {}):
    intent = intents.setdefault(query, {
        'chat_ids': [],
        'inline_message_ids': [],
        'messages': [],
        'priority': 0,
    })

    intent_chat_ids = intent['chat_ids']
    intent_inline_message_ids = intent['inline_message_ids']
    intent_messages = intent['messages']
    
    for item in chat_ids:
        if item not in intent_chat_ids:
            intent_chat_ids.append(item)
    if inline_message_id: intent_inline_message_ids.append(inline_message_id)
    if message: intent_messages.append(message)
    intent['priority'] += len(chat_ids) or 2
    download_video_condition.put_nowait(query)

async def process_intents(bot: Bot):
    while True:
        if not intents:
            await download_video_condition.get()
        if not intents:
            await asyncio.sleep(5)
            continue
        max_priority = max(intents, key=lambda key: intents[key]['priority'])
        await process_query(bot, max_priority)

async def populate_subscriptions():
    while True:
        for url in list(subscriptions.keys()):
            chat_ids = subscriptions[url]['chat_ids']
            if chat_ids: await populate_playlist(url, chat_ids)
            else: subscriptions.pop(url, None)
        await asyncio.sleep(interval_sec)

async def populate_playlist(channel: str, chat_ids: list):
    try:
        info = ydl.extract_info(channel, download=False)
    except:
        print(f"{now()} # populate_playlist error: {channel}")
        return
    entries = info.get('entries')
    if not entries:
        print(f"{now()} # populate_playlist no entries: {channel}")
        return
    for entry in filter_entries(entries)[:5]: await populate_video(entry, chat_ids)

async def populate_video(entry: dict, chat_ids: list):
    query = extract_url(entry)
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

    print(f"{extract_user(user)} # inline_query: {query}")

    if not query: return

    info = extract_info(query, download=False)
    if not info:
        print(f"{now()} # inline_query no info: {query}")
        try: await inline_query.answer(results=[])
        except: pass
        return

    entries = info.get('entries')
    inline_queries = {}

    if entries:
        results = [inline_video(process_info(item), inline_queries) for item in process_entries(entries)]
    else:
        results = [inline_video(info, inline_queries)]

    context.user_data['inline_queries'] = inline_queries

    if not results: print(f"{now()} # inline_query no results: {query}")

    try: await inline_query.answer(results=results, cache_time=1)
    except: pass

async def chosen_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inline_result = update.chosen_inline_result
    inline_message_id = inline_result.inline_message_id
    inline_queries = context.user_data.pop('inline_queries', None)

    if not inline_message_id or not inline_queries: return
    query = inline_queries[inline_result.result_id]
    if not query: return
    user = inline_result.from_user

    print(f"{extract_user(user)} # chosen_query strt: {query}")

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
        print(f"{extract_user(user)} # chosen_query fnsh: {query}")
        return
    
    await append_intent(query, inline_message_id = inline_message_id)
    print(f"{extract_user(user)} # chosen_query aint: {query}")

async def process_query(bot: Bot, query: str) -> dict:
    info = extract_info(query, download=True)
    if not info:
        print(f"{now()} # process_query error: {query}")
        intents.pop(query, None)
        return info
    caption = info.get('caption')
    file_id = info.get('file_id')
    if not file_id:
        try:
            print(f"{now()} # process_query send_video strt: {query}")
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
            print(f"{now()} # process_query send_video fnsh: {query}")
        except: 
            print(f"{now()} # process_query send_video error: {query}")
            intents.pop(query, None)
            return info
        file_id = await post_process(query, info, message)
    
    await process_intent(bot, query, file_id, caption)
    return info

async def process_intent(bot: Bot, query: str, video: str, caption: str) -> dict:
    intent = intents.pop(query, None)
    for item in intent['chat_ids']:
        try: await bot.send_video(chat_id=item, video=video, caption=caption)
        except: print(f"{now()} # process_intent chat_ids error: {query} - {item}")
    for item in intent['inline_message_ids']:
        try: await bot.edit_message_media(inline_message_id=item, media=InputMediaVideo(media=video, caption=caption))
        except: print(f"{now()} # process_intent inline_message_ids error: {query} - {item}")
    for item in intent['messages']:
        try: await bot.edit_message_media(chat_id=item['chat'], message_id=item['message'], media=InputMediaVideo(media=video, caption=caption))
        except: print(f"{now()} # process_intent messages error: {query} - {item}")
    return intent

async def populate_animation(bot: Bot):
    global animation_file_id
    animation_file_id = os.getenv('ANIMATION_FILE_ID')
    if animation_file_id:
        print(f"{now()} # saved animation_file_id = {animation_file_id}")
        return

    query = os.getenv('LOADING_VIDEO_ID')

    info = extract_info(query, download=True)

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
    print(f"{now()} # animation_file_id = {animation_file_id}")

async def subscription_list(update: Update, _: ContextTypes.DEFAULT_TYPE):
    message = update.message
    subscription_list = [f"[{item['title'].replace('[','').replace(']','')}]({item['url']})" for item in user_subscriptions(message.chat_id).values()]

    try:
        if subscription_list: await message.reply_markdown('\n\n'.join(subscription_list))
        else: await message.reply_text('No active subscriptions')
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

    print(f"{extract_user(user)} # download_url: {query}")

    if not query: return ConversationHandler.END

    info = extract_info(query, download=False)
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
    except:
        print(f"{extract_user(user)} # download_url error: {query}")

    users[chat_id] = user.to_dict()
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
            context.user_data['uploader_videos'] = f"{uploader_url}/videos"
            return await subscribe_playlist(update, context)

    except:
        print(f"{extract_user(user)} # subscribe_url failed: {query}")
        await message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    entries = info.get('entries')
    uploader = info.get('uploader') or info.get('uploader_id')
    uploader_videos = f"{uploader_url}/videos"
    if not entries or not uploader or not uploader_videos:
        await message.reply_text("Error occured", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    playlists = {}
    append_playlist(playlists, uploader, uploader_videos)
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
            print(f"{extract_user(user)} # subscribe_playlist failed")
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

    print(f"{extract_user(user)} # subscribe_playlist: {title} - {url}")

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
            print(f"{extract_user(user)} # subscribe_playlist failed: {url}")
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
            print(f"{extract_user(user)} # unsubscribe_playlist failed")
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

    print(f"{extract_user(user)} # unsubscribe_playlist: {query}")

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
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    asyncio.gather(
        populate_animation(bot),
        populate_subscriptions(),
        populate_files(),
        process_intents(bot),
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
