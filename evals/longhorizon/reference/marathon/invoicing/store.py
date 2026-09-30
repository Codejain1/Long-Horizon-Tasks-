"""Where the service keeps its data: in memory, or in a JSON file that several processes share safely."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from typing import Iterator

TABLES = ("customers", "products", "invoices", "payments", "credits", "refunds", "schedules", "audit", "reminders",
          "fx", "counters")
_MEMORY: dict[str, dict] = {}
_PATH: str | None = None
_ACTIVE: dict[str, dict] | None = None


def open_store(path: str | None) -> None:
    """Keep everything in the file at `path` from now on; None goes back to memory."""
    global _PATH
    _PATH = None if path is None else os.fspath(path)


@contextmanager
def transaction() -> Iterator[dict[str, dict]]:
    """The tables, to read and change. A file store stays locked for the whole block and is written back at its
    end, so concurrent processes never lose each other's changes, and an error leaves the file as it was.
    A block inside another shares the outer one."""
    global _ACTIVE
    if _ACTIVE is not None:
        yield _ACTIVE
        return
    if _PATH is None:
        _ACTIVE = _complete(_MEMORY)
        try:
            yield _ACTIVE
        finally:
            _ACTIVE = None
        return
    with open(_PATH + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _ACTIVE = _complete(_read(_PATH))
            yield _ACTIVE
            _write(_PATH, _ACTIVE)
        finally:
            _ACTIVE = None
            fcntl.flock(lock, fcntl.LOCK_UN)


def reset() -> None:
    """Empty every table of the open store."""
    with transaction() as db:
        db.clear()
        _complete(db)


def _complete(db: dict) -> dict:
    for table in TABLES:
        db.setdefault(table, {})
    return db


def _read(path: str) -> dict:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return {}
    with open(path) as f:
        return json.load(f)


def _write(path: str, db: dict) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(db, f)
    os.replace(tmp, path)
