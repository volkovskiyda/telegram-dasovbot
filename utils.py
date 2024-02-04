from time import strftime

def is_not_live(info, *, incomplete):
    if info.get('is_live'): return f"{now()} # ignore live video {info.get('url')}"

ydl_opts = {
    'format': 'mp4',
    'outtmpl': 'videos/%(upload_date)s - %(title).40s [%(id).20s].%(ext)s',
    'noplaylist': True,
    'extract_flat': True,
    'playlist_items': '1-20',
    'match_filter': is_not_live,
    'quiet': True,
    'progress': True,
}

def extract_url(info: dict) -> str:
    return info.get('webpage_url') or info['url']

def now() -> str:
    return strftime('%Y-%m-%d %H:%M:%S')

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
        'caption': f"{info.get('title')}\n{extract_url(info)}",
        'created': now(),
        'requested': now(),
        'url': info.get('url'),
        'filepath': filepath,
        'filename': filename,
        'entries': info.get('entries'),
    }
