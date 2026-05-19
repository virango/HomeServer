FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y exiftool \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY renpix ./
COPY .ExifTool_config ./
COPY watcher.py ./

RUN chmod +x renpix

ENV WATCH_DIR=/watch \
    DEFAULT_DEST_DIR=/photos \
    EVENTS_DB_PATH=/data/events.db \
    POLL_INTERVAL=15

CMD ["python", "watcher.py"]
