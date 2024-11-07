FROM python

RUN mkdir project data
VOLUME /data
VOLUME /media
VOLUME /home

WORKDIR /project
COPY info.py main.py utils.py /project/

RUN python -m pip install --upgrade pip
RUN pip install -U python-dotenv python-telegram-bot yt-dlp ffmpeg

CMD python main.py
