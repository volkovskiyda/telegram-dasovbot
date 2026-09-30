import logging
import os
import time

logger = logging.getLogger(__name__)


def remove(filepath: str):
    try:
        os.remove(filepath)
    except Exception:
        pass


def empty_media_folder_files(media_folder: str):
    for file in os.listdir(media_folder):
        file_path = os.path.join(media_folder, file)
        remove(file_path)


def remove_stale_media_files(media_folder: str, max_age_sec: float) -> list[str]:
    """Remove files in the media folder untouched for longer than max_age_sec.

    A safety net for anything the download pipeline leaks (a crash between
    download and upload, a restart mid-download). Age is taken from the newer
    of mtime and ctime: yt-dlp can backdate mtime to the server's
    Last-Modified header, while ctime still records when the file was written.
    """
    cutoff = time.time() - max_age_sec
    removed = []
    try:
        entries = list(os.scandir(media_folder))
    except FileNotFoundError:
        return removed
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if max(stat.st_mtime, stat.st_ctime) < cutoff:
            remove(entry.path)
            removed.append(entry.name)
    return removed
