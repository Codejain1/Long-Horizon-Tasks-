"""Memory graph storage (MEMROUTER.md §4, build steps 2-7).

- `lessons`: lessons and strategies distilled by the sleep job, plus fear lessons (§3, §9).
- `links`: learned weights between memories: semantic, co-success, derived-from, contradicts.
- `memory_state`: strength and stability for every memory (episodes, lessons, strategies) (§8).
- `episodes_archive`: cold storage; episodes are moved here, never deleted (§7).
- `predictor_stats`: how well each source predicts outcomes (§5 step 4, PredictorStats).
- `consolidation_runs`: when the sleep job ran and what it did.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from horizon.db import Database, load_json
from horizon.models import Lesson, now
from horizon.taskstate.store import ts

SYMMETRIC = {"semantic", "co-success"}


@dataclass
class State:
    strength: float
    stability: float
    recalls: int = 0
    helpful: int = 0


def _canon(a: str, b: str, kind: str) -> tuple[str, str]:
    return (min(a, b), max(a, b)) if kind in SYMMETRIC else (a, b)


class Graph:
    def __init__(self, db: Database, dim: int, default_strength: float = 0.5):
        self.db, self.dim, self.default_strength = db, dim, default_strength
        sql = db.kind == "sqlite"
        text, stamp, real = ("TEXT", "TEXT", "REAL") if sql else ("JSONB", "TIMESTAMPTZ", "DOUBLE PRECISION")
        vec = "BLOB" if sql else f"vector({dim})"
        for stmt in (
            f"""CREATE TABLE IF NOT EXISTS lessons (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT, kind TEXT NOT NULL,
                is_fear {'INTEGER' if sql else 'BOOLEAN'} NOT NULL, data {text} NOT NULL,
                embedding_model TEXT NOT NULL, embedding {vec} NOT NULL, created_at {stamp} NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS links (
                team_id TEXT NOT NULL, from_id TEXT NOT NULL, to_id TEXT NOT NULL, kind TEXT NOT NULL,
                weight {real} NOT NULL, updated_at {stamp} NOT NULL, PRIMARY KEY (from_id, to_id, kind))""",
            f"""CREATE TABLE IF NOT EXISTS memory_state (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, strength {real} NOT NULL, stability {real} NOT NULL,
                recalls INTEGER NOT NULL, helpful INTEGER NOT NULL, updated_at {stamp} NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS predictor_stats (
                team_id TEXT NOT NULL, source TEXT NOT NULL, task_type TEXT NOT NULL, samples INTEGER NOT NULL,
                sum_p {real} NOT NULL, sum_actual {real} NOT NULL, sum_abs_err {real} NOT NULL,
                sum_sq_err {real} NOT NULL, PRIMARY KEY (team_id, source, task_type))""",
            f"""CREATE TABLE IF NOT EXISTS consolidation_runs (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, episodes_seen INTEGER NOT NULL, report {text} NOT NULL,
                created_at {stamp} NOT NULL)""",
            # Same columns as `episodes`, so a move is a straight copy.
            f"""CREATE TABLE IF NOT EXISTS episodes_archive (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT, task_id TEXT,
                data {text} NOT NULL, success {real} NOT NULL, surprise {real} NOT NULL,
                severity TEXT NOT NULL, embedding_model TEXT NOT NULL, embedding {vec} NOT NULL,
                created_at {stamp} NOT NULL, archived_at {stamp})""",
            "CREATE INDEX IF NOT EXISTS lessons_team ON lessons (team_id, embedding_model)",
            "CREATE INDEX IF NOT EXISTS links_to ON links (to_id)",
        ):
            db.execute(stmt)

    # --- lessons and strategies -------------------------------------------------

    def add_lesson(self, lesson: Lesson, model: str, vector: np.ndarray, strength: float, stability: float) -> None:
        self.db.execute(
            "INSERT INTO lessons (id, team_id, project_id, kind, is_fear, data, embedding_model, embedding, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (lesson.id, lesson.scope.team_id, lesson.scope.project_id, lesson.kind, lesson.is_fear,
             self.db.json(lesson.model_dump(mode="json")), model, self.db.vector(vector),
             ts(self.db, lesson.created_at)))
        self.set_state(lesson.id, lesson.scope.team_id, State(strength, stability))

    def save_lesson(self, lesson: Lesson) -> None:
        lesson.updated_at = now()
        self.db.execute("UPDATE lessons SET is_fear = %s, data = %s WHERE id = %s",
                        (lesson.is_fear, self.db.json(lesson.model_dump(mode="json")), lesson.id))

    def get_lesson(self, lesson_id: str, team_id: str) -> Lesson | None:
        row = self.db.fetchone("SELECT data FROM lessons WHERE id = %s AND team_id = %s", (lesson_id, team_id))
        return Lesson.model_validate(load_json(row[0])) if row else None

    def lessons(self, team_id: str, fear_only: bool = False) -> list[Lesson]:
        sql = "SELECT data FROM lessons WHERE team_id = %s" + (" AND is_fear = %s" if fear_only else "")
        params = (team_id, True) if fear_only else (team_id,)
        return [Lesson.model_validate(load_json(r[0])) for r in self.db.fetchall(sql, params)]

    def search_lessons(self, team_id: str, vector: np.ndarray, model: str, k: int) -> list[tuple[Lesson, float]]:
        if self.db.kind == "postgres":
            q = self.db.vector(vector)
            rows = self.db.fetchall(
                "SELECT data, 1 - (embedding <=> %s) FROM lessons WHERE team_id = %s AND embedding_model = %s"
                " ORDER BY embedding <=> %s LIMIT %s", (q, team_id, model, q, k))
            return [(Lesson.model_validate(load_json(d)), float(s)) for d, s in rows]
        rows = self.db.fetchall("SELECT data, embedding FROM lessons WHERE team_id = %s AND embedding_model = %s",
                                (team_id, model))
        if not rows:
            return []
        matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), self.dim)
        sims = matrix @ np.asarray(vector, dtype=np.float32)
        return [(Lesson.model_validate(load_json(rows[i][0])), float(sims[i])) for i in np.argsort(-sims)[:k]]

    # --- links ------------------------------------------------------------------------

    def neighbours(self, ids: list[str], exclude_kinds: tuple[str, ...] = ("contradicts",)) -> list[tuple]:
        """(node, neighbour, kind, weight) for every link touching `ids`, in both directions."""
        if not ids:
            return []
        marks = ", ".join(["%s"] * len(ids))
        rows = self.db.fetchall(f"SELECT from_id, to_id, kind, weight FROM links WHERE from_id IN ({marks})"
                                f" OR to_id IN ({marks})", tuple(ids) * 2)
        wanted, out = set(ids), []
        for a, b, kind, w in rows:
            if kind in exclude_kinds:
                continue
            if a in wanted:
                out.append((a, b, kind, float(w)))
            if b in wanted:
                out.append((b, a, kind, float(w)))
        return out

    def link_weight(self, a: str, b: str, kind: str) -> float | None:
        a, b = _canon(a, b, kind)
        row = self.db.fetchone("SELECT weight FROM links WHERE from_id = %s AND to_id = %s AND kind = %s", (a, b, kind))
        return float(row[0]) if row else None

    def set_link(self, team_id: str, a: str, b: str, kind: str, weight: float) -> None:
        if a == b:
            return
        a, b = _canon(a, b, kind)
        weight = max(0.0, min(1.0, weight))
        if self.link_weight(a, b, kind) is None:
            self.db.execute("INSERT INTO links (team_id, from_id, to_id, kind, weight, updated_at)"
                            " VALUES (%s, %s, %s, %s, %s, %s)", (team_id, a, b, kind, weight, ts(self.db, now())))
        else:
            self.db.execute("UPDATE links SET weight = %s, updated_at = %s WHERE from_id = %s AND to_id = %s"
                            " AND kind = %s", (weight, ts(self.db, now()), a, b, kind))

    def prune_links(self, team_id: str, threshold: float) -> int:
        n = int(self.db.fetchone("SELECT COUNT(*) FROM links WHERE team_id = %s AND weight < %s",
                                 (team_id, threshold))[0])
        self.db.execute("DELETE FROM links WHERE team_id = %s AND weight < %s", (team_id, threshold))
        return n

    def link_count(self, team_id: str) -> int:
        return int(self.db.fetchone("SELECT COUNT(*) FROM links WHERE team_id = %s", (team_id,))[0])

    # --- strength and stability (§8) ---------------------------------------------------

    def states(self, ids: list[str]) -> dict[str, State]:
        if not ids:
            return {}
        marks = ", ".join(["%s"] * len(ids))
        rows = self.db.fetchall(f"SELECT id, strength, stability, recalls, helpful FROM memory_state"
                                f" WHERE id IN ({marks})", tuple(ids))
        found = {r[0]: State(float(r[1]), float(r[2]), int(r[3]), int(r[4])) for r in rows}
        return {i: found.get(i, State(self.default_strength, 1.0)) for i in ids}

    def set_state(self, node_id: str, team_id: str, state: State) -> None:
        params = (max(0.0, min(1.0, state.strength)), state.stability, state.recalls, state.helpful,
                  ts(self.db, now()))
        if self.db.fetchone("SELECT 1 FROM memory_state WHERE id = %s", (node_id,)):
            self.db.execute("UPDATE memory_state SET strength = %s, stability = %s, recalls = %s, helpful = %s,"
                            " updated_at = %s WHERE id = %s", params + (node_id,))
        else:
            self.db.execute("INSERT INTO memory_state (strength, stability, recalls, helpful, updated_at, id, team_id)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s)", params + (node_id, team_id))

    # --- cold storage (§7) --------------------------------------------------------------

    def archive_episode(self, episode_id: str) -> None:
        """Move, never delete: the row is copied to episodes_archive before it leaves `episodes`."""
        cols = ("id, team_id, project_id, task_id, data, success, surprise, severity, embedding_model, embedding,"
                " created_at")
        self.db.execute(f"INSERT INTO episodes_archive ({cols}, archived_at) SELECT {cols}, %s FROM episodes"
                        f" WHERE id = %s", (ts(self.db, now()), episode_id))
        self.db.execute("DELETE FROM episodes WHERE id = %s AND EXISTS (SELECT 1 FROM episodes_archive a"
                        " WHERE a.id = %s)", (episode_id, episode_id))

    def archived_count(self, team_id: str) -> int:
        return int(self.db.fetchone("SELECT COUNT(*) FROM episodes_archive WHERE team_id = %s", (team_id,))[0])

    # --- predictor trust (§5 step 4) -----------------------------------------------------

    def update_predictor(self, team_id: str, source: str, task_type: str, p: float, actual: float) -> None:
        key = (team_id, source, task_type)
        err = abs(p - actual)
        if self.db.fetchone("SELECT 1 FROM predictor_stats WHERE team_id = %s AND source = %s AND task_type = %s",
                            key):
            self.db.execute("UPDATE predictor_stats SET samples = samples + 1, sum_p = sum_p + %s,"
                            " sum_actual = sum_actual + %s, sum_abs_err = sum_abs_err + %s,"
                            " sum_sq_err = sum_sq_err + %s WHERE team_id = %s AND source = %s AND task_type = %s",
                            (p, actual, err, err * err) + key)
        else:
            self.db.execute("INSERT INTO predictor_stats (team_id, source, task_type, samples, sum_p, sum_actual,"
                            " sum_abs_err, sum_sq_err) VALUES (%s, %s, %s, 1, %s, %s, %s, %s)",
                            key + (p, actual, err, err * err))

    def predictor_stats(self, team_id: str) -> list[dict]:
        rows = self.db.fetchall("SELECT source, task_type, samples, sum_p, sum_actual, sum_abs_err, sum_sq_err"
                                " FROM predictor_stats WHERE team_id = %s ORDER BY source, task_type", (team_id,))
        return [{"source": s, "task_type": t, "samples": n,
                 # calibration: mean predicted - mean actual (0 = calibrated; + = overconfident)
                 "calibration": round((sp - sa) / n, 4),
                 "accuracy": round(1 - sae / n, 4),  # 1 - mean absolute error
                 "brier": round(sse / n, 4)}
                for s, t, n, sp, sa, sae, sse in rows]

    # --- consolidation runs ---------------------------------------------------------------

    def log_consolidation(self, run_id: str, team_id: str, episodes_seen: int, report: dict) -> None:
        self.db.execute("INSERT INTO consolidation_runs (id, team_id, episodes_seen, report, created_at)"
                        " VALUES (%s, %s, %s, %s, %s)", (run_id, team_id, episodes_seen, self.db.json(report),
                                                           ts(self.db, now())))

    def last_consolidation_seen(self, team_id: str) -> int:
        row = self.db.fetchone("SELECT MAX(episodes_seen) FROM consolidation_runs WHERE team_id = %s", (team_id,))
        return int(row[0] or 0)
