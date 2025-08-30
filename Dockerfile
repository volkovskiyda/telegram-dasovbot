FROM python

LABEL maintainer="volkovskiyda@gmail.com"
LABEL description="Telegram bot for downloading and sharing online videos"

ARG DEBIAN_FRONTEND=noninteractive

RUN apt update && apt upgrade -y && apt install -y ffmpeg && rm -rf /var/lib/apt/lists/* && apt clean

RUN useradd -m -s /bin/bash localuser
ENV PATH="/home/localuser/.local/bin:${PATH}"

RUN mkdir -p /project /data /media /home && chown -R localuser:localuser /project /data /media /home

WORKDIR /project

RUN python -m pip install --upgrade pip
RUN pip install -U python-dotenv python-telegram-bot yt-dlp python-ffmpeg

USER localuser

COPY --chown=localuser:localuser *.py ./

VOLUME ["/data", "/media", "/home"]

CMD ["python", "main.py"]
