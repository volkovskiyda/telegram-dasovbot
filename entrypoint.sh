#!/bin/sh
if [ -n "$BACKUP_CRON" ]; then
  echo "$BACKUP_CRON /usr/local/bin/python /project/backup.py >> /proc/1/fd/1 2>&1" | crontab -
  cron
fi
exec python main.py
