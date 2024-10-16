from time import strftime

def match_filter(info, *, incomplete):
    if info.get('is_live'): return f"{now()} # ignore video {info.get('url')}"

ydl_opts = {
    'format': 'mp4[height<=?720][filesize_approx<=?2G]',
    'outtmpl': 'videos/%(upload_date)s - %(title).40s [%(id).20s].%(ext)s',
    'noplaylist': True,
    'extract_flat': 'in_playlist',
    'playlist_items': '1-20',
    'match_filter': match_filter,
    'no_warnings': True,
    'quiet': True,
}

def extract_url(info: dict) -> str:
    return info.get('webpage_url') or info['url']

def now() -> str:
    return strftime('%Y-%m-%d %H:%M:%S')

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
    return {
        'file_id': info.get('file_id'),
        'webpage_url': info.get('webpage_url'),
        'title': info.get('title') or url,
        'description': info.get('description'),
        'thumbnail': thumbnail,
        'duration': int(info.get('duration') or 0),
        'uploader_url': info.get('uploader_url'),
        'width': info.get('width'),
        'height': info.get('height'),
        'caption': f"{info.get('title')}\n{url}",
        'url': info.get('url'),
        'filepath': filepath,
        'filename': filename,
        'entries': info.get('entries'),
    }
