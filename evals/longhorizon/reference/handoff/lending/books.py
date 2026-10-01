import uuid

from lending.errors import LendingError
from lending.store import BOOKS


def add_book(title: str, author: str, replacement_cents: int) -> str:
    """`replacement_cents`: what the library pays to replace the book if it's lost."""
    if not title.strip() or not isinstance(replacement_cents, int) or replacement_cents <= 0:
        raise LendingError("a book needs a title and a positive replacement cost in paise")
    book_id = uuid.uuid4().hex
    BOOKS[book_id] = {"id": book_id, "title": title, "author": author, "replacement_cents": replacement_cents}
    return book_id


def get_book(book_id: str) -> dict:
    if book_id not in BOOKS:
        raise LendingError(f"no book {book_id}")
    return dict(BOOKS[book_id])
