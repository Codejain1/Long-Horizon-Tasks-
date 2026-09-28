"""Accounts, API keys, credits and usage (PROJECT.md §3, §10; Phase 7).

- Teams hold API keys. Only a SHA-256 hash and a short prefix of each key are stored; the key itself is
  shown once, when it is created.
- Credits are a ledger (grants and charges), so the balance is always explainable. New teams get a free
  starter grant (§3 "free starter tier").
- Every metered tool call is a usage row with the session it came from and the savings signals it produced,
  which the web page turns into "savings per session" (§3).
- Web sessions for the account page are random tokens, also stored only as hashes.

Local stdio use isn't metered: the host runs on the user's machine with no key.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from horizon.db import Database, load_json
from horizon.models import new_id, now
from horizon.taskstate.store import ts

KEY_PREFIX = "hzn_"
FREE_STARTER_CREDITS = 1000
SESSION_DAYS = 7

# Credits per call (§3: credits pay for Jev scoring, simulation orchestration, memrouter storage/routing and
# context building; §15 leaves pricing open, so these are placeholders). Inspection and approvals are free.
TOOL_CREDITS = {
    "start_task": 1,
    "recall_context": 1,
    "record_outcome": 1,
    "evaluate_options": 5,
    "submit_consequences": 5,
    "explain_decision": 0,
    "show_memories": 0,
    "delete_memory": 0,
    "clear_fear": 0,
    "human_approval": 0,
}


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class OutOfCredits(Exception):
    pass


class Accounts:
    def __init__(self, db: Database):
        self.db = db
        sql = db.kind == "sqlite"
        text, stamp = ("TEXT", "TEXT") if sql else ("JSONB", "TIMESTAMPTZ")
        for stmt in (
            f"CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at {stamp} NOT NULL)",
            f"""CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
                label TEXT, created_at {stamp} NOT NULL, last_used_at {stamp}, revoked_at {stamp})""",
            f"""CREATE TABLE IF NOT EXISTS credit_ledger (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, delta INTEGER NOT NULL, reason TEXT NOT NULL,
                created_at {stamp} NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS usage (
                id TEXT PRIMARY KEY, team_id TEXT NOT NULL, session_id TEXT, task_id TEXT, tool TEXT NOT NULL,
                credits INTEGER NOT NULL, signals {text}, created_at {stamp} NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS web_sessions (
                token_hash TEXT PRIMARY KEY, team_id TEXT NOT NULL, expires_at {stamp} NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS usage_team ON usage (team_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ledger_team ON credit_ledger (team_id, created_at)",
        ):
            db.execute(stmt)

    # --- teams and keys -------------------------------------------------------------

    def create_team(self, name: str, credits: int = FREE_STARTER_CREDITS) -> tuple[str, str]:
        """Returns (team_id, the first API key). The key is not stored and can't be shown again."""
        team_id = new_id("team")
        self.db.execute("INSERT INTO teams (id, name, created_at) VALUES (%s, %s, %s)",
                        (team_id, name, ts(self.db, now())))
        if credits:
            self.grant(team_id, credits, "free starter credits")
        return team_id, self.create_key(team_id, "first key")[1]

    def team(self, team_id: str) -> dict | None:
        row = self.db.fetchone("SELECT id, name, created_at FROM teams WHERE id = %s", (team_id,))
        return {"id": row[0], "name": row[1], "created_at": str(row[2])} if row else None

    def create_key(self, team_id: str, label: str | None = None) -> tuple[str, str]:
        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = new_id("key")
        self.db.execute("INSERT INTO api_keys (id, team_id, key_hash, prefix, label, created_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s)",
                        (key_id, team_id, _hash(raw), raw[:len(KEY_PREFIX) + 6], label, ts(self.db, now())))
        return key_id, raw

    def verify_key(self, raw: str | None) -> str | None:
        """The team the key belongs to, or None. Revoked keys don't verify."""
        if not raw or not raw.startswith(KEY_PREFIX):
            return None
        row = self.db.fetchone("SELECT id, team_id FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL",
                               (_hash(raw),))
        if not row:
            return None
        self.db.execute("UPDATE api_keys SET last_used_at = %s WHERE id = %s", (ts(self.db, now()), row[0]))
        return row[1]

    def keys(self, team_id: str) -> list[dict]:
        rows = self.db.fetchall("SELECT id, prefix, label, created_at, last_used_at, revoked_at FROM api_keys"
                                " WHERE team_id = %s ORDER BY created_at", (team_id,))
        return [{"id": r[0], "prefix": r[1] + "…", "label": r[2], "created_at": str(r[3]),
                 "last_used_at": r[4] and str(r[4]), "revoked": r[5] is not None} for r in rows]

    def revoke_key(self, team_id: str, key_id: str) -> bool:
        exists = self.db.fetchone("SELECT 1 FROM api_keys WHERE id = %s AND team_id = %s AND revoked_at IS NULL",
                                  (key_id, team_id))
        if exists:
            self.db.execute("UPDATE api_keys SET revoked_at = %s WHERE id = %s", (ts(self.db, now()), key_id))
        return bool(exists)

    # --- credits ------------------------------------------------------------------------

    def grant(self, team_id: str, credits: int, reason: str) -> None:
        self.db.execute("INSERT INTO credit_ledger (id, team_id, delta, reason, created_at) VALUES (%s, %s, %s, %s, %s)",
                        (new_id("cr"), team_id, credits, reason, ts(self.db, now())))

    def balance(self, team_id: str) -> int:
        return int(self.db.fetchone("SELECT COALESCE(SUM(delta), 0) FROM credit_ledger WHERE team_id = %s",
                                    (team_id,))[0])

    def ledger(self, team_id: str, limit: int = 20) -> list[dict]:
        rows = self.db.fetchall("SELECT delta, reason, created_at FROM credit_ledger WHERE team_id = %s AND"
                                " reason NOT LIKE %s ORDER BY created_at DESC LIMIT %s", (team_id, "call:%", limit))
        return [{"delta": r[0], "reason": r[1], "at": str(r[2])} for r in rows]

    def check(self, team_id: str, tool: str) -> None:
        cost = TOOL_CREDITS.get(tool, 1)
        if cost and self.balance(team_id) < cost:
            raise OutOfCredits(f"Out of Horizon credits ({tool} costs {cost}). Top up on the account page, "
                               f"then retry. Carry on with the task without Horizon meanwhile.")

    def charge(self, team_id: str, tool: str, *, session_id: str | None, task_id: str | None,
               signals: dict | None = None) -> None:
        cost = TOOL_CREDITS.get(tool, 1)
        if cost:
            self.db.execute("INSERT INTO credit_ledger (id, team_id, delta, reason, created_at)"
                            " VALUES (%s, %s, %s, %s, %s)", (new_id("cr"), team_id, -cost, f"call:{tool}",
                                                               ts(self.db, now())))
        self.db.execute("INSERT INTO usage (id, team_id, session_id, task_id, tool, credits, signals, created_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (new_id("use"), team_id, session_id, task_id, tool, cost, self.db.json(signals or {}),
                         ts(self.db, now())))

    # --- usage and savings ------------------------------------------------------------------

    def usage(self, team_id: str, days: int = 30) -> list[dict]:
        since = ts(self.db, now() - timedelta(days=days))
        rows = self.db.fetchall("SELECT tool, COUNT(*), SUM(credits) FROM usage WHERE team_id = %s AND"
                                " created_at >= %s GROUP BY tool ORDER BY tool", (team_id, since))
        return [{"tool": t, "calls": int(n), "credits": int(c or 0)} for t, n, c in rows]

    def savings(self, team_id: str, limit: int = 20) -> list[dict]:
        """Per session: what Horizon did that a plain agent wouldn't have (§3 "show savings per session").

        A session is the MCP session when the transport has one (protocol <= 2025-11-25 streamable HTTP); the
        2026-07-28 transport is stateless, so there a Horizon task stands in for it.

        Only counted events, no guesses: decisions scored, close calls settled from memory (spikes avoided),
        options eliminated before implementing, regressions caught by a rollback, fear warnings shown,
        human approvals asked. Tokens saved are reported only where spikes reported their token cost.
        """
        rows = self.db.fetchall("SELECT session_id, task_id, tool, credits, signals, created_at FROM usage"
                                " WHERE team_id = %s ORDER BY created_at", (team_id,))
        sessions: dict[str, dict] = {}
        for session_id, task_id, tool, credits, signals, at in rows:
            key = session_id or f"task:{task_id}" if (session_id or task_id) else "unknown"
            s = sessions.setdefault(key, {
                "session": key, "started_at": str(at), "tasks": set(), "calls": 0, "credits": 0,
                "decisions_scored": 0, "settled_from_memory": 0, "options_eliminated": 0,
                "regressions_caught": 0, "fear_warnings": 0, "human_approvals": 0, "tokens_saved_estimate": 0})
            sig = load_json(signals) or {}
            s["calls"] += 1
            s["credits"] += int(credits)
            s["last_at"] = str(at)
            if task_id:
                s["tasks"].add(task_id)
            s["decisions_scored"] += tool == "evaluate_options" and sig.get("decision") not in (None, "unscored")
            s["settled_from_memory"] += sig.get("settled_by") == "memory"
            s["options_eliminated"] += int(sig.get("eliminated", 0))
            s["regressions_caught"] += sig.get("rollback") in ("rollback", "escalate")
            s["fear_warnings"] += int(sig.get("fear_warnings", 0))
            s["human_approvals"] += int(sig.get("human_approvals", 0))
            s["tokens_saved_estimate"] += int(sig.get("spike_tokens_avoided", 0))
        out = sorted(sessions.values(), key=lambda s: s["last_at"], reverse=True)[:limit]
        for s in out:
            s["tasks"] = len(s["tasks"])
        return out

    # --- web sessions ---------------------------------------------------------------------

    def open_session(self, team_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO web_sessions (token_hash, team_id, expires_at) VALUES (%s, %s, %s)",
                        (_hash(token), team_id, ts(self.db, now() + timedelta(days=SESSION_DAYS))))
        return token

    def session_team(self, token: str | None) -> str | None:
        if not token:
            return None
        row = self.db.fetchone("SELECT team_id FROM web_sessions WHERE token_hash = %s AND expires_at > %s",
                               (_hash(token), ts(self.db, now())))
        return row[0] if row else None

    def close_session(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM web_sessions WHERE token_hash = %s", (_hash(token),))
