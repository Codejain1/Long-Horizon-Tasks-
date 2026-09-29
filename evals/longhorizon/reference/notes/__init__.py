import json
import sqlite3


def _db(store_path):
    con = sqlite3.connect(str(store_path), timeout=60, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT, tags TEXT)")
    return con


def add_note(store_path, text, tags):
    with _db(store_path) as con:
        return con.execute("INSERT INTO notes (text, tags) VALUES (?, ?)", (text, json.dumps(list(tags)))).lastrowid


def get_note(store_path, note_id):
    row = _db(store_path).execute("SELECT text, tags FROM notes WHERE id = ?", (note_id,)).fetchone()
    return {"text": row[0], "tags": json.loads(row[1])} if row else None


def list_notes(store_path, tag=None):
    rows = _db(store_path).execute("SELECT id, tags FROM notes ORDER BY id").fetchall()
    return [i for i, t in rows if tag is None or tag in json.loads(t)]


def search(store_path, query):
    words = query.lower().split()
    rows = _db(store_path).execute("SELECT id, text FROM notes ORDER BY id").fetchall()
    return [i for i, t in rows if all(w in t.lower().split() for w in words)]


def edit_note(store_path, note_id, text=None, tags=None):
    con = _db(store_path)
    if text is not None:
        con.execute("UPDATE notes SET text = ? WHERE id = ?", (text, note_id))
    if tags is not None:
        con.execute("UPDATE notes SET tags = ? WHERE id = ?", (json.dumps(list(tags)), note_id))


def delete_note(store_path, note_id):
    _db(store_path).execute("DELETE FROM notes WHERE id = ?", (note_id,))
