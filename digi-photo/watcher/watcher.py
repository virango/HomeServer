import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

from watchfiles import Change, watch

WATCH_DIR = Path(os.getenv("WATCH_DIR", "/watch"))
DEFAULT_DEST_DIR = Path(os.getenv("DEFAULT_DEST_DIR", "/photos"))
ROOT_MEDIA_DIR = Path(os.getenv("ROOT_MEDIA_DIR", str(DEFAULT_DEST_DIR)))
EVENTS_DB_PATH = Path(os.getenv("EVENTS_DB_PATH", "/data/events.db"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

SUPPORTED_EXTENSIONS = {"cr3", "jpg", "cr2", "mov"}
TEMP_FILE_SUFFIXES = {".part", ".tmp", ".partial"}
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


def is_temporary_file(path: Path) -> bool:
    return any(path.name.lower().endswith(suffix) for suffix in TEMP_FILE_SUFFIXES)


def is_supported_file(path: Path) -> bool:
    return (
        path.is_file()
        and not is_temporary_file(path)
        and path.suffix.lstrip(".").lower() in SUPPORTED_EXTENSIONS
    )


def is_file_stable(path: Path, check_interval: float = 1.0, checks: int = 3) -> bool:
    try:
        previous = path.stat()
    except OSError:
        return False

    for _ in range(checks):
        time.sleep(check_interval)
        try:
            current = path.stat()
        except OSError:
            return False
        if current.st_size != previous.st_size or current.st_mtime != previous.st_mtime:
            return False
        previous = current
    return True


RENAME_PATTERN = re.compile(r"^'(?P<old>[^']+)' --> '(?P<new>[^']+)'$")
META_WARNING_PATTERN = re.compile(r"Error reading meta data", re.I)


def normalize_watch_path(path: Path) -> str:
    try:
        return str(path.relative_to(WATCH_DIR))
    except ValueError:
        return str(path)


def run_renpix(files: list[Path]) -> list[Path]:
    renamed_paths: list[Path] = []
    for file_path in files:
        if not file_path.exists():
            continue

        relative_path = normalize_watch_path(file_path)
        print(f"Running renpix for {relative_path} in {WATCH_DIR}", flush=True)
        try:
            completed = subprocess.run(
                ["/app/renpix", relative_path],
                cwd=WATCH_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            print(f"Failed to run renpix: {exc}", flush=True)
            continue

        if completed.stdout:
            print(completed.stdout, end="", flush=True)
        if completed.stderr:
            print(completed.stderr, end="", flush=True)

        final_path = None
        for line in completed.stdout.splitlines():
            match = RENAME_PATTERN.match(line.strip())
            if match:
                final_path = WATCH_DIR / match.group("new")
                break

        metadata_error = any(
            META_WARNING_PATTERN.search(line) for line in completed.stderr.splitlines()
        )
        if metadata_error:
            print(
                f"Warning: metadata error detected for {relative_path}; skipping move until file is complete.",
                flush=True,
            )
            continue

        if final_path is None:
            final_path = WATCH_DIR / relative_path

        if final_path.exists() and is_supported_file(final_path):
            renamed_paths.append(final_path)
        else:
            print(
                f"Warning: expected renamed file not found for {relative_path}, "
                f"falling back to original path.",
                flush=True,
            )
            if file_path.exists():
                renamed_paths.append(file_path)

    return renamed_paths


def move_photos(files: list[Path] | None = None) -> None:
    if files is None:
        files = [p for p in WATCH_DIR.iterdir() if is_supported_file(p) and is_file_stable(p)]
    else:
        files = [p for p in files if p.exists() and is_supported_file(p)]

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

        print(f"Moving {file_path} -> {dest_path}", flush=True)
        try:
            original_stat = file_path.stat()
            print(
                f"Original timestamps for {file_path}: atime={original_stat.st_atime}, "
                f"mtime={original_stat.st_mtime}, ctime={original_stat.st_ctime}",
                flush=True,
            )
        except Exception as exc:
            original_stat = None
            print(f"Warning: could not stat source file {file_path}: {exc}", flush=True)

        shutil.move(str(file_path), str(dest_path))

        if original_stat is not None:
            try:
                os.utime(str(dest_path), ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                restored_stat = dest_path.stat()
                print(
                    f"Restored timestamps for {dest_path}: atime={restored_stat.st_atime}, "
                    f"mtime={restored_stat.st_mtime}, ctime={restored_stat.st_ctime}",
                    flush=True,
                )
            except Exception as exc:
                print(f"Warning: could not preserve timestamp for {dest_path}: {exc}", flush=True)
        else:
            print(f"Skipped timestamp preservation for {dest_path} because source stat failed.", flush=True)


def scan_initial_files() -> list[Path]:
    return [p for p in WATCH_DIR.iterdir() if is_supported_file(p) and is_file_stable(p)]


def main() -> None:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_DEST_DIR.mkdir(parents=True, exist_ok=True)
    ensure_database()

    print("Starting watcher service", flush=True)

    initial_files = scan_initial_files()
    if initial_files:
        renamed = run_renpix(initial_files)
        move_photos(renamed)

    try:
        for changes in watch(WATCH_DIR, debounce=1000, recursive=False):
            event_paths: set[Path] = set()
            for change, path_str in changes:
                if change not in {Change.added, Change.modified}:
                    continue
                path = Path(path_str)
                if is_supported_file(path):
                    event_paths.add(path)

            stable_files = [path for path in event_paths if is_file_stable(path)]
            if stable_files:
                renamed = run_renpix(stable_files)
                move_photos(renamed)
    except KeyboardInterrupt:
        print("Watcher stopped by user", flush=True)
    except Exception as exc:
        print(f"Watcher error: {exc}", flush=True)


if __name__ == "__main__":
    main()
