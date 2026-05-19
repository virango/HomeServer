FROM python:3.12-slim

WORKDIR /app

COPY web/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY web/app.py ./
COPY web/static ./static

ENV EVENTS_DB_PATH=/data/events.db \
    DEFAULT_DEST_DIR=/photos \
    WEB_PORT=8080

EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
