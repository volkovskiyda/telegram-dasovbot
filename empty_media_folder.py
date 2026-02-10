from dasovbot.config import load_config
from dasovbot.persistence import empty_media_folder_files

if __name__ == "__main__":
    config = load_config()
    empty_media_folder_files(config.media_folder)
