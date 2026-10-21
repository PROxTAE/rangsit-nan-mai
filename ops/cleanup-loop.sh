#!/bin/sh
# รัน cleanup.py ทุกวันเวลา 03:00 (ตาม TZ ของ container) ใช้แทน cron ใน service cleanup ของ docker-compose.yml
while :; do
    now=$(date +%s)
    next=$(date -d "today 03:00" +%s)
    [ "$next" -le "$now" ] && next=$(date -d "tomorrow 03:00" +%s)
    echo "cleanup ครั้งถัดไป: $(date -d "@$next" '+%Y-%m-%d %H:%M')"
    sleep $((next - now))
    python cleanup.py
done
