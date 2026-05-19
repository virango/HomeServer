import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

WATCH_DIR = Path(os.getenv("WATCH_DIR", "/watch"))
DEFAULT_DEST_DIR = Path(os.getenv("DEFAULT_DEST_DIR", "/photos"))
ROOT_MEDIA_DIR = Path(os.getenv("ROOT_MEDIA_DIR", str(DEFAULT_DEST_DIR)))
EVENTS_DB_PATH = Path(os.getenv("EVENTS_DB_PATH", "/data/events.db"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

SUPPORTED_EXTENSIONS = {"cr3", "CR3", "jpg", "JPG", "cr2", "CR2", "mov", "MOV"}
FILENAME_DATE_PATTERN = re.compile(r"^(?P<date>\d{8}_\d{4})")


def ensure_database() -> None:
    EVENTS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(EVENTS_DB_PATH)
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


def parse_file_date(path: Path) -> str | None:
    match = FILENAME_DATE_PATTERN.match(path.name)
    if not match:
        return None
    date_string = match.group("date")
    try:
        file_dt = datetime.strptime(date_string, "%Y%m%d_%H%M")
        return file_dt.date().isoformat()
    except ValueError:
        return None


def find_event_target(file_date: str) -> str | None:
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        cursor = conn.execute(
            "SELECT target_dir FROM events WHERE ? BETWEEN start_date AND end_date ORDER BY start_date LIMIT 1",
            (file_date,),
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def sanitize_relative_path(value: str) -> Path:
    clean = value.strip()
    if not clean:
        return Path("unknown")

    parts = [re.sub(r"[^A-Za-z0-9ÄÖÜäöüß _-]", "_", part.strip()) for part in re.split(r"[\\/]+", clean) if part.strip()]
    return Path(*parts) if parts else Path("unknown")


def get_exif_tag(file_path: Path, tags: list[str]) -> str:
    for tag in tags:
        try:
            result = subprocess.run(
                ["exiftool", "-s3", f"-{tag}", str(file_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return ""
        value = result.stdout.strip()
        if value:
            return value
    return ""


def get_photographer_firstname(file_path: Path) -> str:
    value = get_exif_tag(file_path, ["Photographer", "Creator", "Artist"])
    if not value:
        return "Unknown"
    return sanitize_relative_path(value.split()[0]).name or "Unknown"


def build_destination_dir(file_path: Path, file_date: str | None, event_dir: str | None) -> Path:
    photographer = get_photographer_firstname(file_path)
    year = file_date[:4] if file_date else str(datetime.fromtimestamp(file_path.stat().st_mtime).year)
    event_segment = sanitize_relative_path(event_dir or "unsorted")
    return ROOT_MEDIA_DIR / photographer / year / event_segment


def run_renpix() -> None:
    print(f"Running renpix in {WATCH_DIR}")
    try:
        subprocess.run(["/app/renpix"], cwd=WATCH_DIR, check=False)
    except Exception as exc:
        print(f"Failed to run renpix: {exc}")


def move_photos() -> None:
    files = [p for p in WATCH_DIR.iterdir() if p.is_file() and p.suffix.lstrip(".") in SUPPORTED_EXTENSIONS]
    if not files:
        return

    for file_path in files:
        file_date = parse_file_date(file_path)
        target_dir = find_event_target(file_date) if file_date else None
        destination = build_destination_dir(file_path, file_date, target_dir)
        destination.mkdir(parents=True, exist_ok=True)

        dest_path = destination / file_path.name
        if dest_path.exists():
            suffix = 1
            while True:
                new_name = f"{file_path.stem}-{suffix}{file_path.suffix}"
                candidate = destination / new_name
                if not candidate.exists():
                    dest_path = candidate
                    break
                suffix += 1

        print(f"Moving {file_path} -> {dest_path}")
        shutil.move(str(file_path), str(dest_path))


def main() -> None:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_DEST_DIR.mkdir(parents=True, exist_ok=True)
    ensure_database()

    print("Starting watcher service")
    while True:
        try:
            run_renpix()
            move_photos()
        except Exception as exc:
            print(f"Watcher error: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
