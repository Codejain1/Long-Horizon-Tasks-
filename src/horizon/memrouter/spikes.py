"""Past spike results (MEMROUTER.md §12 `lookupSimulation`, §13 "instead of simulations").

When a close call was already tested with a spike, the result is reused instead of spiking again
(PROJECT.md §6 step 2). Stored per option, embedded as "situation + option" so a similar decision
finds it; the option label must also match.
"""

from __future__ import annotations

import numpy as np

from horizon.db import Database, load_json
from horizon.models import new_id, now
from horizon.taskstate.store import ts


def spike_text(situation: str, option: str) -> str:
    return f"{situation.strip()}\noption: {option.strip()}"


def same_option(a: str | None, b: str | None) -> bool:
    """Labels get reworded a little between calls ("sqlite3" vs "sqlite3 (stdlib)")."""
    if not a or not b:
        return False
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or a in b or b in a


class SpikeStore:
    def __init__(self, db: Database, dim: int):
        self.db, self.dim = db, dim
        vec = "BLOB" if db.kind == "sqlite" else f"vector({dim})"
        text, stamp = ("TEXT", "TEXT") if db.kind == "sqlite" else ("JSONB", "TIMESTAMPTZ")
        db.execute(f"""CREATE TABLE IF NOT EXISTS spike_results (
            id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT, situation TEXT NOT NULL,
            option TEXT NOT NULL, result {text} NOT NULL, embedding_model TEXT NOT NULL,
            embedding {vec} NOT NULL, created_at {stamp} NOT NULL)""")
        db.execute("CREATE INDEX IF NOT EXISTS spikes_team ON spike_results (team_id, embedding_model)")

    def add(self, team_id: str, project_id: str | None, situation: str, option: str, result: dict,
            model: str, vector: np.ndarray) -> str:
        sid = new_id("spk")
        self.db.execute(
            "INSERT INTO spike_results (id, team_id, project_id, situation, option, result, embedding_model,"
            " embedding, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, team_id, project_id, situation, option, self.db.json(result), model, self.db.vector(vector),
             ts(self.db, now())))
        return sid

    def search(self, team_id: str, vector: np.ndarray, model: str, k: int = 10) -> list[tuple[dict, float]]:
        """Nearest past spikes, best first: ({situation, option, result, created_at, id}, similarity)."""
        cols = "situation, option, result, created_at, id"
        if self.db.kind == "postgres":
            q = self.db.vector(vector)
            rows = self.db.fetchall(
                f"SELECT {cols}, 1 - (embedding <=> %s) FROM spike_results WHERE team_id = %s AND"
                " embedding_model = %s ORDER BY embedding <=> %s LIMIT %s", (q, team_id, model, q, k))
            sims = [float(r[5]) for r in rows]
        else:
            rows = self.db.fetchall(f"SELECT {cols}, embedding FROM spike_results WHERE team_id = %s AND"
                                    " embedding_model = %s", (team_id, model))
            if not rows:
                return []
            matrix = np.frombuffer(b"".join(r[5] for r in rows), dtype=np.float32).reshape(len(rows), self.dim)
            all_sims = matrix @ np.asarray(vector, dtype=np.float32)
            order = np.argsort(-all_sims)[:k]
            rows, sims = [rows[i] for i in order], [float(all_sims[i]) for i in order]
        return [({"situation": r[0], "option": r[1], "result": load_json(r[2]), "created_at": str(r[3]), "id": r[4]}, s)
                for r, s in zip(rows, sims)]
