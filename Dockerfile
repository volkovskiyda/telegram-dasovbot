FROM python

RUN apt update && apt upgrade -y && apt clean

RUN mkdir project data
VOLUME /data
VOLUME /media
VOLUME /home

WORKDIR /project
COPY info.py main.py utils.py empty_media_folder.py /project/

RUN apt update && apt install -y ffmpeg
RUN python -m pip install --upgrade pip
RUN pip install -U python-dotenv python-telegram-bot yt-dlp python-ffmpeg

CMD ["python", "main.py"]
