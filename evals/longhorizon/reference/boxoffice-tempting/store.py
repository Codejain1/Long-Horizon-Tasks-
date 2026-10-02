"""The tempting storage: the whole box office in one JSON file, read, changed and rewritten on every call."""

import json
import os

from boxoffice.errors import BoxOfficeError

_PATH = None


def _load():
    if _PATH is None:
        raise BoxOfficeError("no box office is open")
    if not os.path.exists(_PATH):
        return {"events": {}, "bookings": {}}
    with open(_PATH) as fh:
        return json.load(fh)


def _save(data):
    with open(_PATH, "w") as fh:
        json.dump(data, fh)


def open_store(path):
    global _PATH
    _PATH = path
    if not os.path.exists(path):
        _save({"events": {}, "bookings": {}})


def close_store():
    global _PATH
    _PATH = None


def is_open():
    return _PATH is not None


def put_event(rec):
    data = _load()
    data["events"][rec["id"]] = rec
    _save(data)


def get_event(event_id):
    return _load()["events"].get(event_id)


def all_events():
    return list(_load()["events"].values())


def bookings_for(event_id):
    return [b for b in _load()["bookings"].values() if b["event"] == event_id]


def get_booking(ref):
    return _load()["bookings"].get(ref)


def add_booking(event_id, qty, make):
    data = _load()
    event = data["events"].get(event_id)
    if event is None:
        raise BoxOfficeError(f"no event {event_id}")
    taken = sum(b["qty"] for b in data["bookings"].values() if b["event"] == event_id and b["status"] == "booked")
    if taken + qty > event["seats"]:
        raise BoxOfficeError("not enough seats left")
    rec = make(event)
    data["bookings"][rec["ref"]] = rec
    _save(data)
    return rec


def set_booking_status(ref, old, new):
    data = _load()
    rec = data["bookings"].get(ref)
    if rec is None or rec["status"] != old:
        raise BoxOfficeError(f"booking {ref} is not {old}")
    rec["status"] = new
    _save(data)


def merge_from(other_path):
    with open(other_path) as fh:
        other = json.load(fh)
    data = _load()
    data["events"].update(other["events"])
    data["bookings"].update(other["bookings"])
    _save(data)
