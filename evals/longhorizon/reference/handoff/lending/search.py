from typing import List

from lending.store import BOOKS


def find_books(query: str) -> List[dict]:
    q = query.casefold()
    hits = [dict(b) for b in BOOKS.values() if q in b["title"].casefold() or q in b["author"].casefold()]
    return sorted(hits, key=lambda b: b["title"].casefold())
