"""World-model decision log (PROJECT.md §6: "log data in a trainable format from day one").

One record per decision: the state the scorer saw, every raw scorer answer from both passes, the
consequence plan and results, the final decision, and the outcomes the host later recorded. Records are
versioned JSON (`schema`/`version`) and export as JSON Lines, one training example per line:
state -> option -> consequences -> outcome.
"""

from __future__ import annotations

from collections.abc import Iterator

from horizon.db import Database, load_json
from horizon.models import new_id, now
from horizon.taskstate.store import ts

SCHEMA = "horizon.decision"
VERSION = 1


def new_record(*, team_id: str, project_id: str | None, task_id: str, state: dict, options: list[dict],
               scorer: dict) -> dict:
    return {"schema": SCHEMA, "version": VERSION, "id": new_id("dec"), "team_id": team_id,
            "project_id": project_id, "task_id": task_id, "created_at": now().isoformat(), "state": state,
            "options": options, "scorer": scorer, "passes": [], "consequences": None, "final": None,
            "stage": "open", "outcomes": []}


class DecisionLog:
    def __init__(self, db: Database):
        self.db = db
        text, stamp = ("TEXT", "TEXT") if db.kind == "sqlite" else ("JSONB", "TIMESTAMPTZ")
        db.execute(f"""CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY, team_id TEXT NOT NULL, task_id TEXT NOT NULL, stage TEXT NOT NULL,
            data {text} NOT NULL, created_at {stamp} NOT NULL, updated_at {stamp} NOT NULL)""")
        db.execute("CREATE INDEX IF NOT EXISTS decisions_team ON decisions (team_id, created_at)")

    def save(self, record: dict) -> dict:
        stamp = ts(self.db, now())
        exists = self.db.fetchone("SELECT 1 FROM decisions WHERE id = %s", (record["id"],))
        if exists:
            self.db.execute("UPDATE decisions SET stage = %s, data = %s, updated_at = %s WHERE id = %s",
                            (record["stage"], self.db.json(record), stamp, record["id"]))
        else:
            self.db.execute(
                "INSERT INTO decisions (id, team_id, task_id, stage, data, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (record["id"], record["team_id"], record["task_id"], record["stage"], self.db.json(record),
                 stamp, stamp))
        return record

    def get(self, decision_id: str, team_id: str) -> dict | None:
        row = self.db.fetchone("SELECT data FROM decisions WHERE id = %s AND team_id = %s", (decision_id, team_id))
        return load_json(row[0]) if row else None

    def export(self, team_id: str | None = None, with_outcomes_only: bool = False) -> Iterator[dict]:
        sql, params = "SELECT data FROM decisions", ()
        if team_id is not None:
            sql, params = sql + " WHERE team_id = %s", (team_id,)
        for (data,) in self.db.fetchall(sql + " ORDER BY created_at", params):
            record = load_json(data)
            if not with_outcomes_only or record["outcomes"]:
                yield record
