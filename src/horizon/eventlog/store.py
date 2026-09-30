"""Append-only event storage on SQLite or Postgres (v2 Phase 0).

`n` is the insertion order, so a session's events replay in the order the hooks wrote them even when two
share a timestamp. The text fields are redacted again here: events can come from any adapter.
"""

from __future__ import annotations

from horizon.db import Database, load_json
from horizon.eventlog.schema import Event
from horizon.redact import MAX_NOTE, redact
from horizon.taskstate.store import ts

MAX_TEXT = 1000
MAX_TARGET = 200

_SCHEMA = {
    "sqlite": """CREATE TABLE IF NOT EXISTS events (
        n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, team_id TEXT NOT NULL, project TEXT NOT NULL,
        session_id TEXT, kind TEXT NOT NULL, domain TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "postgres": """CREATE TABLE IF NOT EXISTS events (
        n BIGSERIAL PRIMARY KEY, id TEXT NOT NULL UNIQUE, team_id TEXT NOT NULL, project TEXT NOT NULL,
        session_id TEXT, kind TEXT NOT NULL, domain TEXT NOT NULL, data JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL)""",
}
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS events_session ON events (team_id, project, session_id, n)",
    "CREATE INDEX IF NOT EXISTS events_project ON events (team_id, project, created_at)",
]


def sanitise(event: Event) -> Event:
    event = event.model_copy(deep=True)
    event.text = redact(event.text, MAX_TEXT)
    if event.action is not None:
        event.action.target = redact(event.action.target, MAX_TARGET)
    if event.result is not None:
        event.result.facts = {k: _clean(v) for k, v in event.result.facts.items()}
    event.data = {k: _clean(v) for k, v in event.data.items()}
    return event


def _clean(value):
    if isinstance(value, str):
        return redact(value, MAX_NOTE)
    if isinstance(value, list):
        return [_clean(v) for v in value[:50]]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in list(value.items())[:50]}
    return value


class EventStore:
    def __init__(self, db: Database):
        self.db = db
        db.execute(_SCHEMA[db.kind])
        for stmt in _INDEXES:
            db.execute(stmt)

    def append(self, event: Event) -> Event:
        event = sanitise(event)
        self.db.execute(
            "INSERT INTO events (id, team_id, project, session_id, kind, domain, data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (event.id, event.team_id, event.project, event.session_id, event.kind, event.domain,
             self.db.json(event.model_dump(mode="json")), ts(self.db, event.at)))
        return event

    def session(self, team_id: str, project: str, session_id: str | None) -> list[Event]:
        """A session's events in the order they were written."""
        where, params = "team_id = %s AND project = %s AND ", (team_id, project)
        if session_id is None:
            where += "session_id IS NULL"
        else:
            where, params = where + "session_id = %s", params + (session_id,)
        rows = self.db.fetchall(f"SELECT data FROM events WHERE {where} ORDER BY n", params)
        return [Event.model_validate(load_json(r[0])) for r in rows]

    def sessions(self, team_id: str, project: str, limit: int = 50) -> list[str]:
        """The project's most recent sessions, newest first."""
        rows = self.db.fetchall(
            "SELECT session_id, MAX(n) AS last FROM events WHERE team_id = %s AND project = %s"
            " AND session_id IS NOT NULL GROUP BY session_id ORDER BY last DESC LIMIT %s", (team_id, project, limit))
        return [r[0] for r in rows]

    def count(self, team_id: str) -> int:
        return int(self.db.fetchone("SELECT COUNT(*) FROM events WHERE team_id = %s", (team_id,))[0])
