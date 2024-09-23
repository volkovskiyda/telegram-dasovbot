FROM python:alpine

RUN mkdir project
WORKDIR /project
VOLUME /project/config
VOLUME /project/videos
COPY info.py main.py utils.py /project/

RUN apk -U add bash
RUN python -m pip install --upgrade pip
RUN pip install -U python-dotenv python-telegram-bot --pre "yt-dlp[default]"

CMD python main.py
