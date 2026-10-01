"""In-memory data. A new kind of record gets its own table from `table()`, so `reset()` empties it too."""

from typing import Dict, List

_TABLES: List[dict] = []


def table() -> Dict[str, dict]:
    t: Dict[str, dict] = {}
    _TABLES.append(t)
    return t


def reset() -> None:
    for t in _TABLES:
        t.clear()


MEMBERS = table()
BOOKS = table()
