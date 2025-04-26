import os, json, traceback
from time import strftime
from datetime import datetime

def match_filter(info, *, incomplete):
    if info.get('is_live') or int(info.get('duration') or 0) > 10_000:
        return f"{now()} # ignore_video {info.get('url')}"

config_folder = os.getenv('CONFIG_FOLDER') or '/'
video_info_file = f'{config_folder}/data/videos.json'
user_info_file = f'{config_folder}/data/users.json'
subscription_info_file = f'{config_folder}/data/subscriptions.json'
new_subscriptions_file = f'{config_folder}/data/new_subscriptions.txt'
intent_info_file = f'{config_folder}/data/intents.json'
timestamp_file = f'{config_folder}/data/timestamp.txt'

datetime_format = '%Y%m%d_%H%M%S'

ydl_opts = {
    'format': 'bv*[ext=mp4][height<=?720][filesize_approx<=?2G]+ba[ext=m4a]/bv*[ext=mp4][height<=?720][filesize_approx<=?2G]+ba[ext=mp4]/b[ext=mp4][height<=?720][filesize_approx<=?2G]',
    'outtmpl': f'{config_folder}/media/%(timestamp>{datetime_format})s - %(title).80s [%(id).20s].%(ext)s',
    'noplaylist': True,
    'extract_flat': 'in_playlist',
    'playlist_items': '1-20',
    'match_filter': match_filter,
    'no_warnings': True,
    'quiet': True,
    'postprocessors': [{'key': 'FFmpegMetadata'}],
}

def extract_url(info: dict) -> str:
    return info.get('webpage_url') or info['url']

def now() -> str:
    return strftime(datetime_format)

def process_info(info: dict) -> dict:
    if not info: return None
    requested_downloads_list = info.get('requested_downloads')
    if requested_downloads_list:
        requested_downloads = requested_downloads_list[0]
        filepath = requested_downloads['filepath']
        filename = requested_downloads['filename']
    else:
        filepath = None
        filename = None
    url = extract_url(info)
    id = info.get('id')
    if id: thumbnail = f"https://i.ytimg.com/vi/{id}/default.jpg"
    else: thumbnail = info.get('thumbnail')
    timestamp = info.get('timestamp')
    if timestamp: timestamp = datetime.fromtimestamp(timestamp).strftime(datetime_format)
    return {
        'file_id': info.get('file_id'),
        'webpage_url': info.get('webpage_url'),
        'title': info.get('title') or url,
        'description': info.get('description'),
        'upload_date': info.get('upload_date'),
        'timestamp': timestamp,
        'thumbnail': thumbnail,
        'duration': int(info.get('duration') or 0),
        'uploader_url': info.get('uploader_url'),
        'width': info.get('width'),
        'height': info.get('height'),
        'caption': f"{info.get('title')}\n{url}",
        'url': info.get('url'),
        'filepath': filepath,
        'filename': filename,
        'format': info.get('format'),
        'entries': info.get('entries'),
    }

def write_file(file_path, dict):
    try:
        file = open(file_path, "w", encoding='utf8')
        json.dump(dict, file, indent=1, ensure_ascii=False)
        file.write('\r')
    except Exception as e:
        traceback.print_exception(e)

def read_file(file_path, dict) -> dict:
    try:
        with open(file_path, "r", encoding='utf8') as file:
            return json.load(file)
    except Exception as e:
        traceback.print_exception(e)
        write_file(file_path, dict)
        return {}
