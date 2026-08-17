#!/bin/sh
if [ -n "$BACKUP_CRON" ]; then
  # Debian cron does not inherit the container environment: bake the backup
  # settings into the crontab, or backup.py silently runs with its defaults
  {
    [ -n "$BACKUP_MAX_COUNT" ] && echo "BACKUP_MAX_COUNT=$BACKUP_MAX_COUNT"
    [ -n "$DB_PATH" ] && echo "DB_PATH=$DB_PATH"
    [ -n "$BACKUP_DIR" ] && echo "BACKUP_DIR=$BACKUP_DIR"
    echo "$BACKUP_CRON /usr/local/bin/python /project/backup.py >> /proc/1/fd/1 2>&1"
  } | crontab -
  cron
fi
exec python main.py
