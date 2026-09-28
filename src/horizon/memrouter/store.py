"""Episode storage and similarity search (MEMROUTER.md §4, build step 1)."""

from __future__ import annotations

import numpy as np

from horizon.db import Database, load_json
from horizon.models import Episode, now
from horizon.taskstate.store import ts


def _schema(kind: str, dim: int) -> list[str]:
    if kind == "sqlite":
        return [
            """CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT, task_id TEXT,
                data TEXT NOT NULL, success REAL NOT NULL, surprise REAL NOT NULL,
                severity TEXT NOT NULL, embedding_model TEXT NOT NULL, embedding BLOB NOT NULL,
                created_at TEXT NOT NULL, archived_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS recall_log (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, task_id TEXT, situation TEXT NOT NULL,
                episode_ids TEXT NOT NULL, created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS episodes_team ON episodes (team_id, embedding_model)",
        ]
    return [
        f"""CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT, task_id TEXT,
            data JSONB NOT NULL, success DOUBLE PRECISION NOT NULL, surprise DOUBLE PRECISION NOT NULL,
            severity TEXT NOT NULL, embedding_model TEXT NOT NULL, embedding vector({dim}) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, archived_at TIMESTAMPTZ)""",
        """CREATE TABLE IF NOT EXISTS recall_log (
            id TEXT PRIMARY KEY, team_id TEXT NOT NULL, task_id TEXT, situation TEXT NOT NULL,
            episode_ids JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS episodes_team ON episodes (team_id, embedding_model)",
        "CREATE INDEX IF NOT EXISTS episodes_embedding ON episodes USING hnsw (embedding vector_cosine_ops)",
    ]


class EpisodeStore:
    def __init__(self, db: Database, dim: int):
        self.db = db
        self.dim = dim
        for stmt in _schema(db.kind, dim):
            db.execute(stmt)

    def add(self, episode: Episode, vector: np.ndarray) -> None:
        if vector.shape != (self.dim,):
            raise ValueError(f"embedding has shape {vector.shape}, expected ({self.dim},)")
        self.db.execute(
            "INSERT INTO episodes (id, team_id, project_id, task_id, data, success, surprise, severity,"
            " embedding_model, embedding, created_at, archived_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (episode.id, episode.scope.team_id, episode.scope.project_id, episode.scope.task_id,
             self.db.json(episode.model_dump(mode="json")), episode.actual.success, episode.surprise,
             episode.severity, episode.embedding_model, self.db.vector(vector),
             ts(self.db, episode.provenance.created_at), ts(self.db, episode.archived_at)),
        )

    def get(self, episode_id: str, team_id: str) -> Episode | None:
        """Also finds archived episodes: evidence references stay valid (MEMROUTER.md §7)."""
        for table in ("episodes", "episodes_archive"):
            try:
                row = self.db.fetchone(f"SELECT data FROM {table} WHERE id = %s AND team_id = %s", (episode_id, team_id))
            except Exception:  # the archive table exists once the graph is set up
                row = None
            if row:
                return Episode.model_validate(load_json(row[0]))
        return None

    def get_many(self, ids: list[str], team_id: str) -> dict[str, Episode]:
        if not ids:
            return {}
        marks = ", ".join(["%s"] * len(ids))
        rows = self.db.fetchall(f"SELECT data FROM episodes WHERE team_id = %s AND id IN ({marks})",
                                (team_id, *ids))
        return {e.id: e for e in (Episode.model_validate(load_json(r[0])) for r in rows)}

    def recent(self, team_id: str, limit: int) -> list[Episode]:
        rows = self.db.fetchall("SELECT data FROM episodes WHERE team_id = %s ORDER BY created_at DESC LIMIT %s",
                                (team_id, limit))
        return [Episode.model_validate(load_json(r[0])) for r in rows]

    def vectors(self, ids: list[str]) -> dict[str, np.ndarray]:
        if not ids:
            return {}
        marks = ", ".join(["%s"] * len(ids))
        rows = self.db.fetchall(f"SELECT id, embedding FROM episodes WHERE id IN ({marks})", tuple(ids))
        if self.db.kind == "postgres":
            return {i: np.asarray(v.to_numpy() if hasattr(v, "to_numpy") else v, dtype=np.float32) for i, v in rows}
        return {i: np.frombuffer(v, dtype=np.float32) for i, v in rows}

    def search(
        self, team_id: str, vector: np.ndarray, model: str, k: int, project_id: str | None = None
    ) -> list[tuple[Episode, float]]:
        """Top-k non-archived episodes in the team by cosine similarity, best first.

        Only episodes embedded with the same model are comparable, so others are skipped.
        """
        where = "team_id = %s AND embedding_model = %s AND archived_at IS NULL"
        params: tuple = (team_id, model)
        if project_id is not None:
            where += " AND project_id = %s"
            params += (project_id,)

        if self.db.kind == "postgres":
            q = self.db.vector(vector)
            rows = self.db.fetchall(
                f"SELECT data, 1 - (embedding <=> %s) FROM episodes WHERE {where}"
                " ORDER BY embedding <=> %s LIMIT %s",
                (q,) + params + (q, k),
            )
            return [(Episode.model_validate(load_json(d)), float(s)) for d, s in rows]

        rows = self.db.fetchall(f"SELECT data, embedding FROM episodes WHERE {where}", params)
        if not rows:
            return []
        matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), self.dim)
        sims = matrix @ np.asarray(vector, dtype=np.float32)
        order = np.argsort(-sims)[:k]
        return [(Episode.model_validate(load_json(rows[i][0])), float(sims[i])) for i in order]

    def log_recall(self, recall_id: str, team_id: str, task_id: str | None, situation: str,
                   episode_ids: list[str]) -> None:
        self.db.execute(
            "INSERT INTO recall_log (id, team_id, task_id, situation, episode_ids, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (recall_id, team_id, task_id, situation, self.db.json(episode_ids), ts(self.db, now())),
        )

    def get_recall(self, recall_id: str) -> list[str] | None:
        row = self.db.fetchone("SELECT episode_ids FROM recall_log WHERE id = %s", (recall_id,))
        return load_json(row[0]) if row else None

    def count(self, team_id: str) -> int:
        return int(self.db.fetchone("SELECT COUNT(*) FROM episodes WHERE team_id = %s", (team_id,))[0])
