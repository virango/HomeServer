import os
import sqlite3
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")

events_db_path = os.getenv("EVENTS_DB_PATH", "/data/events.db")

def ensure_database() -> None:
    db_dir = os.path.dirname(events_db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(events_db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                target_dir TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_db_connection():
    conn = sqlite3.connect(events_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def validate_event_payload(data):
    title = data.get("title", "").strip()
    start_date = data.get("start_date", "").strip()
    end_date = data.get("end_date", "").strip() or start_date
    target_dir = data.get("target_dir", "").strip()

    if not title:
        return None, "title is required"
    if not start_date:
        return None, "start_date is required"
    if not target_dir:
        return None, "target_dir is required"

    return {
        "title": title,
        "description": data.get("description", "").strip(),
        "target_dir": target_dir,
        "start_date": start_date,
        "end_date": end_date,
    }, None


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/events", methods=["GET"])
def list_events():
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT * FROM events ORDER BY start_date, title").fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        conn.close()


@app.route("/api/events", methods=["POST"])
def create_event():
    data, error = validate_event_payload(request.json or {})
    if error:
        return jsonify({"error": error}), 400

    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO events (title, description, target_dir, start_date, end_date) VALUES (?, ?, ?, ?, ?)",
            (data["title"], data["description"], data["target_dir"], data["start_date"], data["end_date"]),
        )
        conn.commit()
        event_id = cursor.lastrowid
        return jsonify({"id": event_id, **data}), 201
    finally:
        conn.close()


@app.route("/api/events/<int:event_id>", methods=["PUT"])
def update_event(event_id):
    data, error = validate_event_payload(request.json or {})
    if error:
        return jsonify({"error": error}), 400

    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE events SET title = ?, description = ?, target_dir = ?, start_date = ?, end_date = ? WHERE id = ?",
            (data["title"], data["description"], data["target_dir"], data["start_date"], data["end_date"], event_id),
        )
        conn.commit()
        return jsonify({"id": event_id, **data})
    finally:
        conn.close()


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
        return "", 204
    finally:
        conn.close()


@app.route("/static/<path:path>", methods=["GET"])
def static_files(path):
    return send_from_directory(app.static_folder, path)


if __name__ == "__main__":
    ensure_database()
    app.run(host="0.0.0.0", port=int(os.getenv("WEB_PORT", "8080")))
