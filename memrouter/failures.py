"""Experience, keyed by what failed: every failing check seen in a REAL outcome (hidden tests, a QA report, the
owner's verdict), per scope (a project, a task set). Before the next job in that scope the checks that failed come
back as concrete warnings ("test_x failed 3 times: <error line>: get it right the first time").

Only ground truth is written here, never an agent's opinion of its own work. Each warning shown is logged, and the
next outcome for that scope says whether the warned checks then passed, so a warning that never helps is visible
(`stats`). This replaces the generic episodes/strategies layer, whose lessons were retry labels and raw tracebacks.
"""

from __future__ import annotations

import json
import time


class Failures:
    def __init__(self, db):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS failures (scope TEXT, check_id TEXT, error TEXT, seen INTEGER, "
                   "first_ts REAL, last_ts REAL, warned INTEGER DEFAULT 0, then_passed INTEGER DEFAULT 0, "
                   "PRIMARY KEY (scope, check_id))")
        db.execute("CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY, ts REAL, scope TEXT, checks TEXT, "
                   "settled INTEGER DEFAULT 0)")
        db.commit()

    def record(self, scope: str, failed: list[dict], passed: list[str] = (), warning_id: int | None = None,
               ts: float | None = None) -> dict:
        """One real outcome: `failed` is [{"check", "error"}], `passed` the checks that passed. If the job had been
        shown warning `warning_id`, its checks are credited: did they pass this time?"""
        ts = ts or time.time()
        for f in failed:
            check = str(f.get("check") or "").strip()[:200]
            if not check:
                continue
            err = " ".join(str(f.get("error") or "").split())[:300]
            self.db.execute("INSERT INTO failures (scope, check_id, error, seen, first_ts, last_ts) VALUES (?, ?, ?, 1, ?, ?) "
                            "ON CONFLICT(scope, check_id) DO UPDATE SET seen = seen + 1, last_ts = excluded.last_ts, "
                            "error = CASE WHEN excluded.error != '' THEN excluded.error ELSE error END",
                            (scope, check, err, ts, ts))
        credited = []
        row = self.db.execute("SELECT checks, settled FROM warnings WHERE id = ? AND scope = ?",
                              (warning_id or 0, scope)).fetchone()
        if row and not row[1]:
            ok = set(passed)
            for check in json.loads(row[0]):
                self.db.execute("UPDATE failures SET warned = warned + 1, then_passed = then_passed + ? "
                                "WHERE scope = ? AND check_id = ?", (int(check in ok), scope, check))
                credited.append(check)
            self.db.execute("UPDATE warnings SET settled = 1 WHERE id = ?", (warning_id,))
        self.db.commit()
        return {"recorded": len(failed), "credited": credited}

    def warn(self, scope: str, limit: int = 5) -> tuple[list[dict], int | None]:
        """The checks that failed before in this scope, most often failed first, then most recent.
        ponytail: every past failure stays eligible; drop the ones `stats` shows never help once data says so."""
        if not scope:
            return [], None
        rows = self.db.execute("SELECT check_id, error, seen FROM failures WHERE scope = ? "
                               "ORDER BY seen DESC, last_ts DESC LIMIT ?", (scope, limit)).fetchall()
        if not rows:
            return [], None
        out = [{"check": r[0], "error": r[1], "seen": r[2]} for r in rows]
        wid = self.db.execute("INSERT INTO warnings (ts, scope, checks) VALUES (?, ?, ?)",
                              (time.time(), scope, json.dumps([r[0] for r in rows]))).lastrowid
        self.db.commit()
        return out, wid

    def stats(self, scope: str) -> list[dict]:
        return [{"check": r[0], "seen": r[1], "warned": r[2], "then_passed": r[3]} for r in self.db.execute(
            "SELECT check_id, seen, warned, then_passed FROM failures WHERE scope = ? ORDER BY seen DESC", (scope,))]


def from_pytest(out: str) -> list[dict]:
    """Failing checks and their message from pytest's short summary (`-rA`): FAILED file::test - message."""
    import re

    seen, found = set(), []
    for m in re.finditer(r"^(?:FAILED|ERROR) \S+::(\w+)(?: - (.*))?$", out or "", re.M):
        if m[1] not in seen:
            seen.add(m[1])
            found.append({"check": m[1], "error": (m[2] or "").strip()})
    return found
