import asyncio
import argparse
import json
import yt_dlp
from utils import ydl_opts

ydl_opts.pop('quiet', None)
ydl = yt_dlp.YoutubeDL(ydl_opts)

def json_dumps(info):
    print(json.dumps(info, indent=1, ensure_ascii=False))

def video(info):
    title = info['title']
    description = info['description']
    url = info.get('webpage_url') or info['url']
    duration = int(info.get('duration') or 0)
    format = info.get('format')
    filesize = sizeof_fmt(int(info.get('filesize') or 0))
    download = info.get('url')
    thumbnail = info.get('thumbnail')
    upload_date = info.get('upload_date')
    channel_url = info['channel_url']
    uploader = info['uploader']
    uploader_url = info['uploader_url']
    live_status = info['live_status']
    is_live = info.get('is_live')
    was_live = info.get('was_live')
    entries = info.get('entries')
    video = {
        'title': title,
        'description': description[:50],
        'url': url,
        'duration': duration,
        'download': download,
        'format': format,
        'filesize': filesize,
        'thumbnail': thumbnail,
        'upload_date': upload_date,
        'channel_url': channel_url,
        'uploader': uploader,
        'uploader_url': uploader_url,
        'live_status': live_status,
        'is_live': is_live,
        'was_live': was_live,
        'entries': entries,
    }
    return video

async def info(query: str, download: bool) -> None:
    info = ydl.extract_info(query, download=download)
    # json_dumps(info)
    entries = info.get('entries')
    if entries:
        nested_entries = entries[0].get('entries')
        if nested_entries:
            entries = nested_entries
        output = [video(item) for item in reversed(entries)]
    else:
        output = video(info)
    
    json_dumps(output)

def sizeof_fmt(num, suffix="B"):
    if (num == 0.0): return "N/A"
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('-d','--download',default=False)
    args = parser.parse_args()
    asyncio.run(info(args.url, download=args.download))


if __name__ == "__main__":
    main()
