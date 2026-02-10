import json
import logging
import os

logger = logging.getLogger(__name__)


def write_file(file_path: str, data: dict):
    try:
        file = open(file_path, "w", encoding='utf8')
        json.dump(data, file, indent=1, ensure_ascii=False)
        file.write('\r')
    except Exception as e:
        logger.error("write_file error: %s", file_path, exc_info=e)


def read_file(file_path: str, default: dict) -> dict:
    try:
        with open(file_path, "r", encoding='utf8') as file:
            return json.load(file)
    except Exception as e:
        logger.error("read_file error: %s", file_path, exc_info=e)
        write_file(file_path, default)
        return {}


def remove(filepath: str):
    try:
        os.remove(filepath)
    except:
        pass


def empty_media_folder_files(media_folder: str):
    for file in os.listdir(media_folder):
        file_path = os.path.join(media_folder, file)
        remove(file_path)
