#!/bin/sh
crontab /etc/cron.d/backup-cron
cron
exec python main.py
