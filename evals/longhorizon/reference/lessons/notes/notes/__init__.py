"""The team's notes service: one SQLite file, safe for several worker processes."""

import sqlite3
import uuid


class NotesError(Exception):
    """Anything the notes service refuses to do."""


_DB = None


def open_notes(path):
    """Open the notes stored at `path` and make them current."""
    global _DB
    close_notes()
    _DB = sqlite3.connect(path, timeout=60, isolation_level=None)
    _DB.execute("PRAGMA journal_mode=WAL")
    _DB.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, text TEXT NOT NULL)")


def close_notes():
    """Close the current notes."""
    global _DB
    if _DB is not None:
        _DB.close()
    _DB = None


def add_note(text):
    """Store a note; returns its id."""
    nid = uuid.uuid4().hex
    _DB.execute("INSERT INTO notes VALUES (?, ?)", (nid, text))
    return nid


def get_note(note_id):
    """The note's text."""
    row = _DB.execute("SELECT text FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise NotesError(f"no note {note_id}")
    return row[0]


def list_notes():
    """Every note id."""
    return [r[0] for r in _DB.execute("SELECT id FROM notes")]
