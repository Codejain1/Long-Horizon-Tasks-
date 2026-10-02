"""Storage: one SQLite file per box office, safe for several processes at once."""

import json
import sqlite3

from boxoffice.errors import BoxOfficeError

_DB = None
SCHEMA = """CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookings (ref TEXT PRIMARY KEY, event TEXT NOT NULL, data TEXT NOT NULL);"""


def _db():
    if _DB is None:
        raise BoxOfficeError("no box office is open")
    return _DB


def open_store(path):
    global _DB
    close_store()
    _DB = sqlite3.connect(path, timeout=60, isolation_level=None)
    _DB.execute("PRAGMA journal_mode=WAL")
    _DB.executescript(SCHEMA)


def close_store():
    global _DB
    if _DB is not None:
        _DB.close()
    _DB = None


def is_open():
    return _DB is not None


def put_event(rec):
    _db().execute("INSERT OR REPLACE INTO events VALUES (?, ?)", (rec["id"], json.dumps(rec)))


def get_event(event_id):
    row = _db().execute("SELECT data FROM events WHERE id = ?", (event_id,)).fetchone()
    return json.loads(row[0]) if row else None


def all_events():
    return [json.loads(r[0]) for r in _db().execute("SELECT data FROM events")]


def bookings_for(event_id):
    return [json.loads(r[0]) for r in _db().execute("SELECT data FROM bookings WHERE event = ?", (event_id,))]


def get_booking(ref):
    row = _db().execute("SELECT data FROM bookings WHERE ref = ?", (ref,)).fetchone()
    return json.loads(row[0]) if row else None


def add_booking(event_id, qty, make):
    """Atomically check the seats left and insert the booking `make()` builds."""
    db = _db()
    db.execute("BEGIN IMMEDIATE")
    try:
        event = get_event(event_id)
        if event is None:
            raise BoxOfficeError(f"no event {event_id}")
        taken = sum(b["qty"] for b in bookings_for(event_id) if b["status"] == "booked")
        if taken + qty > event["seats"]:
            raise BoxOfficeError("not enough seats left")
        rec = make(event)
        db.execute("INSERT INTO bookings VALUES (?, ?, ?)", (rec["ref"], event_id, json.dumps(rec)))
        db.execute("COMMIT")
        return rec
    except BaseException:
        db.execute("ROLLBACK")
        raise


def set_booking_status(ref, old, new):
    db = _db()
    db.execute("BEGIN IMMEDIATE")
    try:
        rec = get_booking(ref)
        if rec is None or rec["status"] != old:
            raise BoxOfficeError(f"booking {ref} is not {old}")
        rec["status"] = new
        db.execute("UPDATE bookings SET data = ? WHERE ref = ?", (json.dumps(rec), ref))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def merge_from(other_path):
    other = sqlite3.connect(other_path)
    try:
        events = other.execute("SELECT id, data FROM events").fetchall()
        bookings = other.execute("SELECT ref, event, data FROM bookings").fetchall()
    finally:
        other.close()
    db = _db()
    db.execute("BEGIN IMMEDIATE")
    try:
        db.executemany("INSERT INTO events VALUES (?, ?)", events)
        db.executemany("INSERT INTO bookings VALUES (?, ?, ?)", bookings)
        db.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        db.execute("ROLLBACK")
        raise BoxOfficeError(f"merge clash: {exc}") from exc
