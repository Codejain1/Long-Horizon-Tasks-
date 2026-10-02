from datetime import datetime, timezone

import pytest

import boxoffice

WHEN = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def office(tmp_path):
    """A fresh, current box office for each test (once ticket 2 exists)."""
    boxoffice.reset()
    path = str(tmp_path / "office")
    if hasattr(boxoffice, "open_box_office"):
        boxoffice.open_box_office(path)
    yield path
    if hasattr(boxoffice, "close_box_office"):
        boxoffice.close_box_office()


def event(seats=100, venue="Blue Hall", name="Raga Night", when=WHEN, price=None, currency="INR"):
    e = boxoffice.create_event(name, venue, when, seats)
    if price is not None:
        boxoffice.set_price(e, price, currency)
    return e
