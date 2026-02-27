FROM python:3.12-slim

LABEL maintainer="volkovskiyda@gmail.com"
LABEL description="Telegram bot for downloading and sharing online videos"

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends ffmpeg jq && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /project /data /media /export

WORKDIR /project

RUN python -m pip install --upgrade pip

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

VOLUME ["/data", "/media", "/export"]

ARG DASHBOARD_PORT=8080
EXPOSE ${DASHBOARD_PORT}

CMD ["python", "main.py"]
