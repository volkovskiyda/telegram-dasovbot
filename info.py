import asyncio
import sys
import argparse
import json
import yt_dlp

ydl_opts = {
    'format': 'mp4',
    'outtmpl': 'videos/%(upload_date)s - %(title)s [%(id)s].%(ext)s',
    'noplaylist': True,
    'extract_flat': True,
    'playlist_items': '1-20',
}
ydl = yt_dlp.YoutubeDL(ydl_opts)

def video(info):
    title = info['title']
    url = info.get('webpage_url') or info['url']
    duration = int(info['duration'])
    download = info['url']
    thumbnail = info['thumbnail']
    upload_date = info['upload_date']
    video = {
        'title': title,
        'url': url,
        'duration': duration,
        'download': download,
        'thumbnail': thumbnail,
        'upload_date': upload_date,
    }
    return video

async def info(query: str, download: bool) -> None:
    info = ydl.extract_info(query, download=download)
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
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('-d','--download',default=False)
    args = parser.parse_args()
    asyncio.run(info(args.url, download=args.download))


if __name__ == "__main__":
    main()
