def is_not_live(info, *, incomplete):
    if info.get('is_live'):
        return 'ignore live video'

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
