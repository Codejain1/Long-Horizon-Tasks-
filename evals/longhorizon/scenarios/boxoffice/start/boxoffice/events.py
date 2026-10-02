"""Events, kept in memory for now."""

import uuid
from datetime import timezone

from boxoffice.errors import BoxOfficeError

EVENTS = {}


def stamp(when):
    """An ISO-8601 UTC string ending in Z."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_event(name, venue, starts_at, seats):
    """Create an event with `seats` seats starting at `starts_at` (an aware datetime); returns its id."""
    if starts_at.tzinfo is None or not isinstance(seats, int) or isinstance(seats, bool) or seats <= 0:
        raise BoxOfficeError("starts_at must be aware and seats a positive whole number")
    eid = uuid.uuid4().hex
    EVENTS[eid] = {"id": eid, "name": name, "venue": venue, "starts_at": stamp(starts_at), "seats": seats}
    return eid


def get_event(event_id):
    """The event as a dict."""
    if event_id not in EVENTS:
        raise BoxOfficeError(f"no event {event_id}")
    return dict(EVENTS[event_id])


def reset():
    """Forget everything (tests)."""
    EVENTS.clear()
