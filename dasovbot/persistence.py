import logging
import os
import shutil
import time

logger = logging.getLogger(__name__)


def remove(filepath: str):
    try:
        os.remove(filepath)
    except Exception:
        pass


def move_atomic(src: str, dst: str) -> None:
    """Move src to dst so that dst never exists half-written.

    The export folder is a Syncthing share: a file that appears there is
    shipped to every peer at once, so a cross-filesystem copy-then-delete
    (what shutil.move does when /media and /export are different mounts)
    would sync a truncated video. The data lands in '<dst>.partial' first
    (same directory, so the final step is a rename on every filesystem) and
    is renamed into place in one atomic os.replace. Syncthing ignores
    '**/*.partial' (see README "High availability").

    Runs blocking I/O; call it through run_in_executor. A failure after the
    partial was created removes it and re-raises, so nothing is left behind
    for Syncthing to ignore forever.
    """
    partial = dst + '.partial'
    os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
    try:
        try:
            os.rename(src, partial)
        except OSError:
            # Different filesystem (EXDEV) or a rename the mount does not
            # allow — same fallback as shutil.move: copy into the partial
            # file, then drop the source
            shutil.copy2(src, partial)
            os.remove(src)
        os.replace(partial, dst)
    except Exception:
        remove(partial)
        raise


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
