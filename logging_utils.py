from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path):
    path = Path(path)
    before = path.stat()
    digest = file_hash(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f"File changed while reading: {path}")
    return {"path": str(path.resolve()), "size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest}


def unchanged(snapshot):
    try:
        return fingerprint(snapshot["path"]) == snapshot
    except OSError:
        return False


def artifact_record(path, destination=None):
    path = Path(path)
    return {"path": str(destination or path), "size": path.stat().st_size, "sha256": file_hash(path)}


def artifact_matches(record):
    path = Path(record["path"])
    try:
        return not path.is_symlink() and path.is_file() and path.stat().st_size == record["size"] and file_hash(path) == record["sha256"]
    except OSError:
        return False


def job_key(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class LibraryLock(AbstractContextManager):
    def __init__(self, root):
        self.path = Path(root) / ".streammii.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("Refusing a symlink lock file")
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0, os.SEEK_END)
                if self.handle.tell() == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"Library is in use by another StreamMii process: {self.path.parent}") from error
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


class Journal(AbstractContextManager):
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(directory / "jobs.sqlite3")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, updated TEXT NOT NULL)")
        self.connection.commit()

    def lookup(self, key):
        row = self.connection.execute("SELECT status, data FROM jobs WHERE key=?", (key,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def put(self, key, status, data):
        self.connection.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?)",
                                (key, status, json.dumps(data, ensure_ascii=True), utc_now()))
        self.connection.commit()

    def completed(self, key):
        entry = self.lookup(key)
        if entry and entry[0] in ("pending", "complete"):
            data = entry[1]
            if data.get("artifacts") and all(artifact_matches(a) for a in data["artifacts"]):
                if entry[0] == "pending":
                    self.put(key, "complete", data)
                return data
        return None

    def __exit__(self, *_):
        self.connection.close()
