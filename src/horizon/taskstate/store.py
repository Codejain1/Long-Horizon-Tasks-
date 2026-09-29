"""Task state store (PROJECT.md §8).

Lives outside memrouter so agents keep working on task state if memrouter is
down (MEMROUTER.md §11). Also holds the host-integration records: test runs
captured by hooks and the log of MCP tool calls (for invocation reliability).
"""

from __future__ import annotations

from datetime import datetime

from horizon.db import Database, load_json
from horizon.models import Checkpoint, TaskState, TestCapture, now
from horizon.redact import redact


def ts(db: Database, value: datetime | None):
    if value is None:
        return None
    return value.isoformat() if db.kind == "sqlite" else value


_SCHEMA = {
    "sqlite": [
        """CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT,
            status TEXT NOT NULL, cwd TEXT, data TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS test_captures (
            id TEXT PRIMARY KEY, session_id TEXT, cwd TEXT, command TEXT NOT NULL,
            runner TEXT NOT NULL, passed INTEGER NOT NULL, failed INTEGER NOT NULL,
            total INTEGER NOT NULL, created_at TEXT NOT NULL, consumed_by TEXT, nudged_at TEXT, failing TEXT)""",
        """CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT NOT NULL, task_id TEXT,
            ok INTEGER NOT NULL, error TEXT, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS checkpoints (
            id TEXT PRIMARY KEY, cwd TEXT NOT NULL, data TEXT NOT NULL,
            created_at TEXT NOT NULL, consumed_by TEXT)""",
        """CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY, team_id TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, action TEXT NOT NULL,
            question TEXT NOT NULL, answer TEXT, approver TEXT, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS attempt_usage (
            id TEXT PRIMARY KEY, session_id TEXT, cwd TEXT NOT NULL, tokens INTEGER NOT NULL, latency_ms INTEGER,
            data TEXT NOT NULL, created_at TEXT NOT NULL, consumed_by TEXT)""",
    ],
    "postgres": [
        """CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT,
            status TEXT NOT NULL, cwd TEXT, data JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS test_captures (
            id TEXT PRIMARY KEY, session_id TEXT, cwd TEXT, command TEXT NOT NULL,
            runner TEXT NOT NULL, passed INTEGER NOT NULL, failed INTEGER NOT NULL,
            total INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL, consumed_by TEXT,
            nudged_at TIMESTAMPTZ, failing JSONB)""",
        """CREATE TABLE IF NOT EXISTS tool_calls (
            id BIGSERIAL PRIMARY KEY, tool TEXT NOT NULL, task_id TEXT,
            ok BOOLEAN NOT NULL, error TEXT, created_at TIMESTAMPTZ NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS checkpoints (
            id TEXT PRIMARY KEY, cwd TEXT NOT NULL, data JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, consumed_by TEXT)""",
        """CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY, team_id TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, action TEXT NOT NULL,
            question TEXT NOT NULL, answer TEXT, approver TEXT, created_at TIMESTAMPTZ NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS attempt_usage (
            id TEXT PRIMARY KEY, session_id TEXT, cwd TEXT NOT NULL, tokens BIGINT NOT NULL, latency_ms BIGINT,
            data JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL, consumed_by TEXT)""",
    ],
}
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS tasks_team_status ON tasks (team_id, status)",
    "CREATE INDEX IF NOT EXISTS captures_cwd ON test_captures (cwd, created_at)",
    "CREATE INDEX IF NOT EXISTS checkpoints_cwd ON checkpoints (cwd, created_at)",
    "CREATE INDEX IF NOT EXISTS attempt_usage_cwd ON attempt_usage (cwd, created_at)",
]


class TaskStore:
    def __init__(self, db: Database):
        self.db = db
        for stmt in _SCHEMA[db.kind] + _INDEXES:
            db.execute(stmt)
        self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations for stores created by earlier versions."""
        if self.db.kind == "postgres":
            self.db.execute("ALTER TABLE test_captures ADD COLUMN IF NOT EXISTS nudged_at TIMESTAMPTZ")
            self.db.execute("ALTER TABLE test_captures ADD COLUMN IF NOT EXISTS failing JSONB")
            return
        columns = {r[1] for r in self.db.fetchall("PRAGMA table_info(test_captures)")}
        for column, kind in (("nudged_at", "TEXT"), ("failing", "TEXT")):
            if column not in columns:
                self.db.execute(f"ALTER TABLE test_captures ADD COLUMN {column} {kind}")

    # --- tasks -----------------------------------------------------------------

    def create(self, task: TaskState) -> TaskState:
        self.db.execute(
            "INSERT INTO tasks (id, team_id, project_id, status, cwd, data, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (task.id, task.team_id, task.project_id, task.status, task.cwd,
             self.db.json(task.model_dump(mode="json")), ts(self.db, task.created_at),
             ts(self.db, task.updated_at)),
        )
        return task

    def get(self, task_id: str, team_id: str) -> TaskState | None:
        row = self.db.fetchone("SELECT data FROM tasks WHERE id = %s AND team_id = %s", (task_id, team_id))
        return TaskState.model_validate(load_json(row[0])) if row else None

    def save(self, task: TaskState) -> TaskState:
        task.updated_at = now()
        self.db.execute(
            "UPDATE tasks SET status = %s, data = %s, updated_at = %s WHERE id = %s AND team_id = %s",
            (task.status, self.db.json(task.model_dump(mode="json")), ts(self.db, task.updated_at),
             task.id, task.team_id),
        )
        return task

    def active(self, team_id: str, cwd: str | None = None) -> list[TaskState]:
        """Open tasks: active, or escalated and waiting for a human."""
        sql = "SELECT data FROM tasks WHERE team_id = %s AND status IN ('active', 'escalated')"
        params: tuple = (team_id,)
        if cwd is not None:
            sql += " AND cwd = %s"
            params += (cwd,)
        rows = self.db.fetchall(sql + " ORDER BY updated_at DESC", params)
        return [TaskState.model_validate(load_json(r[0])) for r in rows]

    # --- test captures (PostToolUse hook) --------------------------------------

    def add_capture(self, cap: TestCapture) -> TestCapture:
        self.db.execute(
            "INSERT INTO test_captures (id, session_id, cwd, command, runner, passed, failed, total,"
            " created_at, consumed_by, failing) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (cap.id, cap.session_id, cap.cwd, cap.command, cap.runner, cap.passed, cap.failed,
             cap.total, ts(self.db, cap.created_at), cap.consumed_by,
             None if cap.failing is None else self.db.json(cap.failing)),
        )
        return cap

    def pending_captures(self, cwd: str, since: datetime | None = None) -> list[TestCapture]:
        """Unconsumed captures for a directory, newest first."""
        sql = ("SELECT id, session_id, cwd, command, runner, passed, failed, total, created_at, consumed_by,"
               " nudged_at, failing FROM test_captures WHERE cwd = %s AND consumed_by IS NULL")
        params: tuple = (cwd,)
        if since is not None:
            sql += " AND created_at >= %s"
            params += (ts(self.db, since),)
        rows = self.db.fetchall(sql + " ORDER BY created_at DESC", params)
        keys = ("id", "session_id", "cwd", "command", "runner", "passed", "failed", "total",
                "created_at", "consumed_by", "nudged_at")
        return [TestCapture.model_validate({**dict(zip(keys, r)), "failing": load_json(r[-1])}) for r in rows]

    def consume_captures(self, capture_ids: list[str], episode_id: str) -> None:
        for cid in capture_ids:
            self.db.execute("UPDATE test_captures SET consumed_by = %s WHERE id = %s", (episode_id, cid))

    def mark_nudged(self, capture_ids: list[str]) -> None:
        for cid in capture_ids:
            self.db.execute("UPDATE test_captures SET nudged_at = %s WHERE id = %s", (ts(self.db, now()), cid))

    # --- checkpoints (PreToolUse hook) -----------------------------------------

    def add_checkpoint(self, ckpt: Checkpoint) -> Checkpoint:
        self.db.execute(
            "INSERT INTO checkpoints (id, cwd, data, created_at) VALUES (%s, %s, %s, %s)",
            (ckpt.id, ckpt.cwd, self.db.json(ckpt.model_dump(mode="json")), ts(self.db, ckpt.created_at)),
        )
        return ckpt

    def take_checkpoint(self, cwd: str, since: datetime, task_id: str) -> Checkpoint | None:
        """The newest unclaimed checkpoint for a directory; claims it and any older ones."""
        rows = self.db.fetchall(
            "SELECT id, data FROM checkpoints WHERE cwd = %s AND consumed_by IS NULL AND created_at >= %s"
            " ORDER BY created_at DESC", (cwd, ts(self.db, since)))
        for cid, _ in rows:
            self.db.execute("UPDATE checkpoints SET consumed_by = %s WHERE id = %s", (task_id, cid))
        return Checkpoint.model_validate(load_json(rows[0][1])) if rows else None

    # --- attempt usage (PreToolUse hook on record_outcome) --------------------------------------

    def add_usage(self, cwd: str, session_id: str | None, usage: dict) -> None:
        from horizon.models import new_id

        self.db.execute(
            "INSERT INTO attempt_usage (id, session_id, cwd, tokens, latency_ms, data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (new_id("use"), session_id, cwd, int(usage["tokens"]), usage.get("latency_ms"), self.db.json(usage),
             ts(self.db, now())))

    def take_usage(self, cwd: str, since: datetime, consumer: str) -> dict | None:
        """The newest unclaimed usage for a directory; claims it and any older ones."""
        rows = self.db.fetchall(
            "SELECT id, data FROM attempt_usage WHERE cwd = %s AND consumed_by IS NULL AND created_at >= %s"
            " ORDER BY created_at DESC", (cwd, ts(self.db, since)))
        for uid, _ in rows:
            self.db.execute("UPDATE attempt_usage SET consumed_by = %s WHERE id = %s", (consumer, uid))
        return load_json(rows[0][1]) if rows else None

    # --- human approvals (PROJECT.md §10; §14 "human approval rate should fall") -------------------

    def log_approval(self, team_id: str, task_id: str | None, kind: str, action: str, question: str,
                     answer: str | None = None, approver: str | None = None) -> None:
        from horizon.models import new_id

        self.db.execute("INSERT INTO approvals (id, team_id, task_id, kind, action, question, answer, approver,"
                        " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        # Questions and answers carry host and user text (reasons, guidance): no raw code (§12).
                        (new_id("apr"), team_id, task_id, kind, action, redact(question, 500), redact(answer, 300),
                         approver and redact(approver, 100), ts(self.db, now())))

    def approvals(self, team_id: str) -> list[dict]:
        rows = self.db.fetchall("SELECT task_id, kind, action, answer, approver, created_at FROM approvals"
                                " WHERE team_id = %s ORDER BY created_at", (team_id,))
        return [{"task_id": r[0], "kind": r[1], "action": r[2], "answer": r[3], "approver": r[4], "at": str(r[5])}
                for r in rows]

    # --- invocation log --------------------------------------------------------

    def log_call(self, tool: str, task_id: str | None, ok: bool, error: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO tool_calls (tool, task_id, ok, error, created_at) VALUES (%s, %s, %s, %s, %s)",
            (tool, task_id, ok, error, ts(self.db, now())),
        )

    def stats(self) -> dict:
        calls = {
            tool: {"calls": int(n), "errors": int(n - (ok or 0))}
            for tool, n, ok in self.db.fetchall(
                "SELECT tool, COUNT(*), SUM(CASE WHEN ok THEN 1 ELSE 0 END) FROM tool_calls GROUP BY tool")
        }
        tasks = dict(self.db.fetchall("SELECT status, COUNT(*) FROM tasks GROUP BY status"))
        approvals = dict(self.db.fetchall("SELECT action, COUNT(*) FROM approvals GROUP BY action"))
        captured, consumed = self.db.fetchone(
            "SELECT COUNT(*), SUM(CASE WHEN consumed_by IS NOT NULL THEN 1 ELSE 0 END) FROM test_captures")
        consumed = int(consumed or 0)
        ckpts, claimed = self.db.fetchone(
            "SELECT COUNT(*), SUM(CASE WHEN consumed_by IS NOT NULL THEN 1 ELSE 0 END) FROM checkpoints")
        return {
            "tool_calls": calls,
            "tasks": {k: int(v) for k, v in tasks.items()},
            "test_runs_captured": int(captured),
            "test_runs_recorded": consumed,
            # Share of real test runs the host followed up with record_outcome.
            "outcome_recording_rate": round(consumed / captured, 3) if captured else None,
            # Phase 3: checkpoints captured by the PreToolUse hook, and how many a recall attached to a task.
            "checkpoints_captured": int(ckpts),
            "checkpoints_attached": int(claimed or 0),
            # Phase 7: human approvals asked through MCP, by what the human did (accept / decline / cancel /
            # unsupported when the client can't ask). §14: this rate should fall over time.
            "approvals": {k: int(v) for k, v in approvals.items()},
        }
