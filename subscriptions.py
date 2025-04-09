import argparse, yt_dlp
from utils import ydl_opts, write_file, read_file, subscription_info_file, new_subscriptions_file

ydl = yt_dlp.YoutubeDL(ydl_opts)

subscriptions = {}

def add_subscription(chat_id, url):
    videos = check_subscription_local(chat_id, f"{url}/videos")
    streams = check_subscription_local(chat_id, f"{url}/streams")

    if not videos and not streams:
        try:
            info = ydl.extract_info(url, download=False)
            uploader_url = info.get('uploader_url')
            title = info.get('title')
            uploader = info.get('uploader') or info.get('uploader_id')
            uploader_videos = f"{uploader_url}/videos"

            subscriptions[uploader_videos] = {
                'chat_ids': [chat_id],
                'title': title,
                'uploader': uploader,
                'uploader_videos': uploader_videos,
            }
            print(f"New subscribption to {title} ({uploader})")
        except Exception as e:
            print(f"# subscribe_playlist failed: {url}")

def check_subscription_local(chat_id, url) -> dict:
    subscription = subscriptions.get(url)
    if subscription:
        chat_ids = subscription['chat_ids']
        subscription_info = f"[{subscription['title']}]({url})"
        if chat_id in chat_ids: print(f"Already subscribed to {subscription_info}")
        else:
            chat_ids.append(chat_id)
            print(f"Subscribed to {subscription_info}")
    return subscription
    

def main() -> None:
    global subscriptions

    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--user', help='User')
    parser.add_argument('-s', '--subscriptions', help='File with subscriptions', default=subscription_info_file)
    parser.add_argument('-n', '--new', help='File with new subscriptions', default=new_subscriptions_file)
    args = parser.parse_args()

    user = args.user
    f_subscriptions = args.subscriptions
    f_new = args.new

    if not user:
        parser.print_help()
        return
    
    subscriptions = read_file(f_subscriptions, subscriptions)
    with open(f_new) as file: lines = [line.rstrip() for line in file]
    
    for line in lines:
        if line: add_subscription(user, line)
    
    write_file(f_subscriptions, subscriptions)

if __name__ == "__main__":
    main()
