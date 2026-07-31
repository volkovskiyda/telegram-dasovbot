FROM python:3.12-slim

LABEL maintainer="volkovskiyda@gmail.com"
LABEL description="Telegram bot for downloading and sharing online videos"

ARG DEBIAN_FRONTEND=noninteractive

# Deno: required by yt-dlp for YouTube JS (EJS) extraction
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends ffmpeg jq cron && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /project /data /media /export

# Cap glibc malloc arenas: with the default (8 x cores) every executor thread
# can pin its own heap arena and freed buffers never return to the OS
ENV MALLOC_ARENA_MAX=2

WORKDIR /project

RUN python -m pip install --upgrade pip

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

VOLUME ["/data", "/media", "/export"]

ARG DASHBOARD_PORT=8080
EXPOSE ${DASHBOARD_PORT}

# The dashboard runs inside the bot process, so it doubles as a liveness probe
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('DASHBOARD_PORT', '8080') + '/login', timeout=5)"

CMD ["./entrypoint.sh"]
