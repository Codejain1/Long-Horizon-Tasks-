"""Events and ticket bookings for a chain of venues."""

from boxoffice import store
from boxoffice.bookings import book, cancel, get_booking, sales_report, sold
from boxoffice.errors import BoxOfficeError
from boxoffice.events import create_event, events_at, get_event, quote, set_price


def open_box_office(path):
    """Open the box office stored at `path` (created if missing) and make it the current one."""
    store.open_store(path)


def close_box_office():
    """Close the current box office."""
    store.close_store()


def merge_box_office(other_path):
    """Bring every event and booking from the box office at `other_path` into the current one."""
    store.merge_from(other_path)


def reset():
    """Forget the current box office (tests)."""
    store.close_store()
