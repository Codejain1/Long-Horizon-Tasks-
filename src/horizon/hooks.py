"""Claude Code hooks (PROJECT.md §11): make the key calls happen reliably.

- SessionStart: remind the model of the workflow and surface any active task.
- PostToolUse (Bash): capture real test counts from test commands, so
  record_outcome uses them instead of the model's own summary (PROJECT.md §9).
- Stop: if tests ran but record_outcome wasn't called, block once and ask for it.

Hooks read JSON on stdin and write JSON on stdout. They must never break the
host session: any internal error exits 0 with no output.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from horizon.config import Settings
from horizon.db import connect
from horizon.models import TestCapture
from horizon.taskstate.store import TaskStore
from horizon.testparse import is_test_command, parse_test_output

WORKFLOW = (
    "This project uses the Horizon MCP server (tools: start_task, recall_context, record_outcome). "
    "For any coding task: call start_task first with the user's request verbatim; call recall_context "
    "before each significant decision or edit; call record_outcome after every test run with the real "
    "test counts."
)


def project_dir(payload: dict) -> str:
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())


def _tool_output(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        parts = [response.get(k) for k in ("stdout", "stderr", "output", "content")]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def session_start(payload: dict, store: TaskStore, settings: Settings) -> dict | None:
    lines = [WORKFLOW]
    active = store.active(settings.team_id, cwd=project_dir(payload))
    if active:
        lines.append("Active tasks in this project (continue with recall_context; do not start_task again):")
        lines += [f"- {t.id}: {t.goal[:200]}" for t in active[:3]]
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}}


def post_tool_use(payload: dict, store: TaskStore, settings: Settings) -> dict | None:
    if payload.get("tool_name") != "Bash":
        return None
    command = (payload.get("tool_input") or {}).get("command", "")
    if not is_test_command(command):
        return None
    counts = parse_test_output(_tool_output(payload.get("tool_response")))
    if counts is None:
        return None
    store.add_capture(TestCapture(
        session_id=payload.get("session_id"),
        cwd=project_dir(payload),
        command=command[:300],
        runner=counts.runner,
        passed=counts.passed,
        failed=counts.failed,
        total=counts.total,
    ))
    context = (f"Horizon captured this test run: {counts.passed} passed, {counts.failed} failed. "
               "Call record_outcome for the change you just tested.")
    active = store.active(settings.team_id, cwd=project_dir(payload))
    if not active:
        context += " (No active task: call start_task first.)"
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}


def stop(payload: dict, store: TaskStore, settings: Settings) -> dict | None:
    if payload.get("stop_hook_active"):
        return None  # already blocked once this turn; never loop
    cwd = project_dir(payload)
    for task in store.active(settings.team_id, cwd=cwd):
        pending = store.pending_captures(cwd, since=task.created_at)
        if pending:
            latest = pending[0]
            return {
                "decision": "block",
                "reason": (
                    f"Tests ran ({latest.passed} passed, {latest.failed} failed) but record_outcome "
                    f"has not been called for task {task.id}. Call record_outcome now with the decision "
                    "you tested, then finish."
                ),
            }
    return None


HANDLERS = {"session-start": session_start, "post-tool-use": post_tool_use, "stop": stop}


def run_hook(name: str, stdin: str, settings: Settings | None = None) -> str:
    """Run a hook and return what it should print. Swallows all errors."""
    try:
        payload = json.loads(stdin or "{}")
        settings = settings or Settings.from_env()
        db = connect(settings.db_url)
        try:
            result = HANDLERS[name](payload, TaskStore(db), settings)
        finally:
            db.close()
        return json.dumps(result) if result else ""
    except Exception as exc:  # a hook must never break the host session
        if os.environ.get("HORIZON_DEBUG"):
            print(f"horizon hook {name} failed: {exc!r}", file=sys.stderr)
        return ""
