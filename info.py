import asyncio
import sys
import json
import yt_dlp

ydl_opts = {
    'format': 'worst[height>=480]/mp4',
    'outtmpl': 'videos/%(upload_date)s - %(title)s [%(id)s].%(ext)s',
    'noplaylist': True,
    'extract_flat': True,
    'playlist_items': '1-20',
    'quiet': True,
}
ydl = yt_dlp.YoutubeDL(ydl_opts)

def video(info):
    title = info['title']
    url = info.get('webpage_url') or info['url']
    duration = int(info['duration'])
    video = {
        'title': title,
        'url': url,
        'duration': duration
    }
    try:
        requested_downloads = info['requested_downloads'][0]
        filename = requested_downloads['filename']
        video['requested_downloads'] = requested_downloads
        video['filename'] = filename
    except (AttributeError, KeyError):
        None
    return video

async def info(query: str) -> None:
    info = ydl.extract_info(query, download=False)
    entries = info.get('entries')
    if entries:
        nested_entries = entries[0].get('entries')
        if nested_entries:
            entries = nested_entries
        output = [video(item) for item in reversed(entries)]
    else:
        output = video(info)
    
    print(json.dumps(output, indent=1, ensure_ascii=False))


def main() -> None:
    url = sys.argv[1:][0]
    asyncio.run(info(url))


if __name__ == "__main__":
    main()
