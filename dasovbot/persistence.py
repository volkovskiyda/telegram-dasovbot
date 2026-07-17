import logging
import os

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
