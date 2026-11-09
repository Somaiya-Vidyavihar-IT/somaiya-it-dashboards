"""Data directory layout, atomic JSON writes, run lock and retention."""
import json
import os
import time
from datetime import datetime, timedelta, timezone

LOCK_STALE_SECONDS = 3 * 3600
REFRESH_STALE_SECONDS = 15 * 60  # one switch: login + about a dozen show commands


def _stale(name):
    return REFRESH_STALE_SECONDS if name == "refresh.lock" else LOCK_STALE_SECONDS


class Store:
    def __init__(self, data_dir):
        self.root = data_dir
        for sub in ("", "snapshots", "reports", "state", "captures"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def read(self, *parts, default=None):
        try:
            with open(self.path(*parts), encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def write(self, data, *parts):
        target = self.path(*parts)
        tmp = f"{target}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, target)

    # ---- one run at a time (scheduled and Run now share run.lock; a single-switch refresh uses
    # refresh.lock, so one refresh runs at a time and a full run is never blocked by one) ----
    def acquire(self, info, name="run.lock"):
        lock = self.path("state", name)
        try:
            if time.time() - os.path.getmtime(lock) > _stale(name):
                os.remove(lock)
        except FileNotFoundError:
            pass
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dict(info, pid=os.getpid()), fh)
        return True

    def release(self, name="run.lock"):
        try:
            os.remove(self.path("state", name))
        except FileNotFoundError:
            pass

    def running(self, name="run.lock"):
        lock = self.path("state", name)
        try:
            return time.time() - os.path.getmtime(lock) <= _stale(name)
        except FileNotFoundError:
            return False

    def status(self, **fields):
        cur = self.read("run-status.json", default={}) or {}
        cur.update(fields)
        self.write(cur, "run-status.json")
        return cur

    def audit(self, line):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(self.path("audit.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}\n")

    def prune(self, keep_days):
        cutoff = time.time() - keep_days * 86400
        for sub in ("snapshots", "reports", "captures"):
            folder = self.path(sub)
            for name in os.listdir(folder):
                full = os.path.join(folder, name)
                if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                    os.remove(full)


def stamp(dt=None):
    """Asia/Kolkata timestamp for file names, e.g. 20261105-0800."""
    dt = dt or datetime.now(timezone.utc)
    return (dt + timedelta(hours=5, minutes=30)).strftime("%Y%m%d-%H%M")
