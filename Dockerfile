FROM python

LABEL maintainer="volkovskiyda@gmail.com"
LABEL description="Telegram bot for downloading and sharing online videos"

ARG DEBIAN_FRONTEND=noninteractive

RUN apt update && apt upgrade -y && apt install -y ffmpeg jq && rm -rf /var/lib/apt/lists/* && apt clean

RUN mkdir -p /project /data /media /export

WORKDIR /project

RUN python -m pip install --upgrade pip

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY dasovbot/ ./dasovbot/
COPY main.py info.py subscriptions.py empty_media_folder.py ./

VOLUME ["/data", "/media", "/export"]

ARG DASHBOARD_PORT=8080
EXPOSE ${DASHBOARD_PORT}

CMD ["python", "main.py"]
