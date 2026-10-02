"""Events and prices."""

import uuid
from datetime import timezone

from boxoffice import money, store
from boxoffice.errors import BoxOfficeError


def stamp(when):
    """An ISO-8601 UTC string ending in Z."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_event(name, venue, starts_at, seats):
    """Create an event with `seats` seats starting at `starts_at` (an aware datetime); returns its id."""
    if starts_at.tzinfo is None or not isinstance(seats, int) or isinstance(seats, bool) or seats <= 0:
        raise BoxOfficeError("starts_at must be aware and seats a positive whole number")
    eid = uuid.uuid4().hex
    store.put_event({"id": eid, "name": name, "venue": venue, "starts_at": stamp(starts_at), "seats": seats,
                     "price": None, "currency": None})
    return eid


def _event(event_id):
    rec = store.get_event(event_id)
    if rec is None:
        raise BoxOfficeError(f"no event {event_id}")
    return rec


def get_event(event_id):
    """The event as a dict."""
    rec = _event(event_id)
    return {k: rec[k] for k in ("id", "name", "venue", "starts_at", "seats")}


def events_at(venue):
    """The events at a venue (ignoring case), soonest first."""
    found = [e for e in store.all_events() if e["venue"].lower() == venue.lower()]
    return [get_event(e["id"]) for e in sorted(found, key=lambda e: e["starts_at"])]


def set_price(event_id, amount, currency):
    """Set the price of one seat."""
    rec = _event(event_id)
    rec["price"], rec["currency"] = money.parse(amount, currency), currency
    store.put_event(rec)


def quote_for(rec, qty):
    if rec["price"] is None:
        raise BoxOfficeError("the event has no price")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
        raise BoxOfficeError("qty must be at least 1")
    return {"amount": money.times(rec["price"], qty, rec["currency"]), "currency": rec["currency"]}


def quote(event_id, qty):
    """The price of `qty` seats."""
    return quote_for(_event(event_id), qty)
