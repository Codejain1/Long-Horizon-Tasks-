"""Claude Code hooks (PROJECT.md §11): make the key calls happen reliably.

- SessionStart: remind the model of the workflow and surface any active task.
- PostToolUse (Bash): capture real test counts from test commands, so
  record_outcome uses them instead of the model's own summary (PROJECT.md §9).
- PreToolUse (recall_context): snapshot the project (git + Claude Code checkpoint
  reference) just before each decision, so a failed attempt can be rolled back. After a
  rollback, deny that recall once if the working tree wasn't restored.
- Stop: only when a task has an unrecorded outcome (a test run no record_outcome used),
  block once and ask for it. Each test run is nudged about at most once.

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
from horizon.taskstate.checkpoints import capture, restore_steps, same_tree
from horizon.taskstate.store import TaskStore
from horizon.testparse import is_test_command, parse_test_output

WORKFLOW = (
    "This project uses the Horizon MCP server (tools: start_task, recall_context, evaluate_options, "
    "record_outcome). For any coding task: call start_task first with the user's request verbatim, then run "
    "the full test suite once before any edit; call recall_context before each significant decision or edit; "
    "call evaluate_options before a crucial choice (framework, database, architecture, key library); call "
    "record_outcome after every test run with the real test counts."
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
        lines += [f"- {t.id}: {t.goal[:200]}" + (" [escalated: ask the user how to proceed]"
                                                  if t.status == "escalated" else "") for t in active[:3]]
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
        failing=list(counts.failing) if counts.failing_complete else None,
    ))
    context = (f"Horizon captured this test run: {counts.passed} passed, {counts.failed} failed. "
               "Call record_outcome for the change you just tested.")
    active = store.active(settings.team_id, cwd=project_dir(payload))
    if not active:
        context += " (No active task: call start_task first.)"
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}


def pre_tool_use(payload: dict, store: TaskStore, settings: Settings) -> dict | None:
    """Records a checkpoint reference that the recall_context call about to run attaches to its task.

    Silent, except right after a rollback: if the tree doesn't match the rollback target, the recall is
    denied once with the restore command (PROJECT.md §8: restore, then retry). Never twice in a row.
    """
    if not str(payload.get("tool_name", "")).endswith("__recall_context"):
        return None
    cwd = project_dir(payload)
    ckpt = capture(cwd, payload.get("session_id"), payload.get("transcript_path"))
    for task in store.active(settings.team_id, cwd=cwd):
        if task.restore_check and ckpt and ckpt.git and not same_tree(cwd, ckpt.git.commit, task.restore_check):
            target = next((c for c in task.checkpoints if c.id == task.rollback_to), None)
            task.restore_check = None
            store.save(task)
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Horizon: task {task.id} was rolled back but the files were not restored. Run "
                    f"`{restore_steps(target)['git']}` first, then call recall_context again."),
            }}
    if ckpt:
        store.add_checkpoint(ckpt)
    return None


def stop(payload: dict, store: TaskStore, settings: Settings) -> dict | None:
    """Quiet by design: speaks only when an active task has an unrecorded outcome, i.e. a test run
    from this session that no record_outcome has used, and only once per test run."""
    if payload.get("stop_hook_active"):
        return None  # already blocked once this turn; never loop
    cwd = project_dir(payload)
    session = payload.get("session_id")
    for task in store.active(settings.team_id, cwd=cwd):
        pending = [c for c in store.pending_captures(cwd, since=task.created_at)
                   if c.nudged_at is None and (session is None or c.session_id in (None, session))]
        if pending:
            store.mark_nudged([c.id for c in pending])
            latest = pending[0]
            return {
                "decision": "block",
                "reason": (f"Unrecorded outcome for task {task.id} (tests: {latest.passed} passed, "
                           f"{latest.failed} failed). Call record_outcome, then finish."),
            }
    return None


HANDLERS = {"session-start": session_start, "pre-tool-use": pre_tool_use, "post-tool-use": post_tool_use,
            "stop": stop}


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
