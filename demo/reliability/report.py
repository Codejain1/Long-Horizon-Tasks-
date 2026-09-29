"""Measure how reliably Claude Code called Horizon during the demo tasks.

Usage: python report.py OUT_DIR

Reads, from OUT_DIR:
- logs/<task>.jsonl        Claude Code transcripts (``claude -p --output-format stream-json``); optional
- logs/<task>.check.txt    the task's own test run after the session; optional
- horizon.db               the Horizon store used during the run (DB-side counts)

Writes OUT_DIR/report.json and prints a table.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HORIZON = "mcp__horizon__"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
TEST_CMD = re.compile(r"\b(pytest|py\.test|unittest)\b")


def _events(transcript: Path):
    for line in transcript.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content if isinstance(content, str) else ""


def tool_calls(transcript: Path) -> list[dict]:
    """Tool calls in order: [{"name": ..., "input": {...}, "result": str | None}]."""
    calls, by_id = [], {}
    for event in _events(transcript):
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                call = {"name": block.get("name", ""), "input": block.get("input") or {}, "result": None,
                        "error": False}
                calls.append(call)
                by_id[block.get("id")] = call
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                if block.get("tool_use_id") in by_id:
                    by_id[block["tool_use_id"]]["result"] = _result_text(block)
                    by_id[block["tool_use_id"]]["error"] = bool(block.get("is_error"))
    return calls


def hook_events(transcript: Path) -> dict:
    """Hook firings seen in the transcript (needs `claude -p --include-hook-events`): {event: {ok, failed}}."""
    fired: dict = {}
    for event in _events(transcript):
        if event.get("type") == "system" and event.get("subtype") == "hook_response":
            counts = fired.setdefault(event.get("hook_event", "?"), {"ok": 0, "failed": 0})
            counts["ok" if event.get("outcome") == "success" and str(event.get("exit_code")) == "0" else "failed"] += 1
    return fired


def aborted(transcript: Path) -> str | None:
    """Sessions the host cut short (e.g. a Claude Code usage limit): not a Horizon result either way."""
    for event in _events(transcript):
        if event.get("type") == "result":
            text = str(event.get("result") or "").lower()
            if "session limit" in text or "usage limit" in text or "rate limit" in text:
                return "usage limit"
    return None


def rollback_action(call: dict) -> str | None:
    """The `rollback.action` a record_outcome call returned, if any."""
    try:
        out = json.loads(call.get("result") or "")
    except json.JSONDecodeError:
        return None
    out = out.get("result", out) if isinstance(out, dict) else {}
    return ((out or {}).get("rollback") or {}).get("action")


def analyse(calls: list[dict]) -> dict:
    """Did the session follow the Horizon workflow?"""
    names = [c["name"] for c in calls]

    def first(pred) -> int | None:
        return next((i for i, c in enumerate(calls) if pred(c)), None)

    def last(pred) -> int | None:
        idx = [i for i, c in enumerate(calls) if pred(c)]
        return idx[-1] if idx else None

    first_edit = first(lambda c: c["name"] in EDIT_TOOLS)
    start = first(lambda c: c["name"] == HORIZON + "start_task")
    recall = first(lambda c: c["name"] == HORIZON + "recall_context")
    last_test = last(lambda c: c["name"] == "Bash" and not c.get("error")  # a denied command never ran
                     and TEST_CMD.search(str(c["input"].get("command", ""))))
    last_record = last(lambda c: c["name"] == HORIZON + "record_outcome")
    before_edit = (lambda i: i is not None and (first_edit is None or i < first_edit))
    # Baseline (PROJECT.md §9): a test run that happened after start_task and before the first recall.
    ran_test = [i for i, c in enumerate(calls) if c["name"] == "Bash" and not c.get("error")
                and TEST_CMD.search(str(c["input"].get("command", "")))]
    baseline_run = start is not None and any(start < i < (recall if recall is not None else len(calls))
                                             for i in ran_test)

    # Phase 3: after a record_outcome that returned a rollback, did the host restore and recall before editing again?
    rollback_at = first(lambda c: c["name"] == HORIZON + "record_outcome" and rollback_action(c) == "rollback")
    restored = recalled = None
    if rollback_at is not None:
        after = calls[rollback_at + 1:]
        next_edit = next((i for i, c in enumerate(after) if c["name"] in EDIT_TOOLS), len(after))
        before_next_edit = after[:next_edit]
        restored = any(c["name"] == "Bash" and "restore --source" in str(c["input"].get("command", ""))
                       for c in before_next_edit)
        recalled = any(c["name"] == HORIZON + "recall_context" for c in before_next_edit)
    return {
        "tool_calls": len(calls),
        "start_task": names.count(HORIZON + "start_task"),
        "recall_context": names.count(HORIZON + "recall_context"),
        "record_outcome": names.count(HORIZON + "record_outcome"),
        "evaluate_options": names.count(HORIZON + "evaluate_options"),
        "evaluated_before_edit": before_edit(first(lambda c: c["name"] == HORIZON + "evaluate_options")),
        "started_before_edit": before_edit(start),
        "baseline_run": baseline_run,
        "recalled_before_edit": before_edit(recall),
        "recorded_after_last_test": last_test is None or (last_record is not None and last_record > last_test),
        "ran_tests": last_test is not None,
        "task_complete_sent": any(c["name"] == HORIZON + "record_outcome" and c["input"].get("task_complete")
                                  for c in calls),
        "rollback_returned": rollback_at is not None,
        "restored_after_rollback": restored,
        "recalled_after_rollback": recalled,
    }


def check_passed(path: Path) -> bool | None:
    if not path.exists():
        return None
    return bool(re.search(r"\d+ passed", path.read_text())) and not re.search(r"\d+ (failed|error)", path.read_text())


def db_stats(out: Path) -> dict | None:
    db = out / "horizon.db"
    if not db.exists():
        return None
    from horizon.db import connected
    from horizon.taskstate.store import TaskStore

    with connected(f"sqlite:///{db}") as conn:
        return TaskStore(conn).stats()


RATE_KEYS = ("started_before_edit", "baseline_run", "recalled_before_edit", "recorded_after_last_test", "task_complete_sent")


def build_report(out: Path) -> dict:
    tasks = {}
    for transcript in sorted((out / "logs").glob("*.jsonl")):
        task_id = transcript.stem
        calls = tool_calls(transcript)
        row = analyse(calls)
        row["aborted"] = aborted(transcript)
        row["hooks"] = hooks = hook_events(transcript)
        # Every hook should fire where its trigger happened: once per session, per recall, per Bash call, per stop.
        expected = {"SessionStart": 1, "PreToolUse": row["recall_context"],
                    # PostToolUse fires only for tool calls that ran (not denied or errored).
                    "PostToolUse": sum(c["name"] == "Bash" and not c["error"] for c in calls), "Stop": 1}
        row["hooks_fired_as_expected"] = bool(hooks) and all(
            hooks.get(ev, {}).get("ok", 0) >= n and not hooks.get(ev, {}).get("failed") for ev, n in expected.items())
        row["tests_pass_after"] = check_passed(out / "logs" / f"{task_id}.check.txt")
        tasks[task_id] = row
    counted = {k: t for k, t in tasks.items() if not t["aborted"]}  # aborted sessions are reported, not scored
    n = len(counted)
    rates = {k: round(sum(bool(t[k]) for t in counted.values()) / n, 2) for k in RATE_KEYS} if n else {}
    if n:
        rates["hooks_fired_as_expected"] = round(sum(t["hooks_fired_as_expected"] for t in counted.values()) / n, 2)
        rolled = [t for t in counted.values() if t["rollback_returned"]]
        if rolled:
            rates["restored_after_rollback"] = round(sum(bool(t["restored_after_rollback"]) for t in rolled) / len(rolled), 2)
            rates["recalled_after_rollback"] = round(sum(bool(t["recalled_after_rollback"]) for t in rolled) / len(rolled), 2)
        rates["tasks_passing"] = round(sum(t["tests_pass_after"] is True for t in counted.values()) / n, 2)
    return {"tasks": tasks, "rates": rates, "horizon_db": db_stats(out)}


def print_report(report: dict) -> None:
    cols = ("start_task", "recall_context", "record_outcome", "started_before_edit", "baseline_run",
            "recalled_before_edit",
            "recorded_after_last_test", "hooks_fired_as_expected", "rollback_returned",
            "restored_after_rollback", "recalled_after_rollback", "tests_pass_after")
    print("task".ljust(14) + "".join(c[:14].rjust(16) for c in cols))
    for task_id, row in report["tasks"].items():
        print(task_id.ljust(14) + "".join(str(row[c]).rjust(16) for c in cols))
    print("\nrates:", json.dumps(report["rates"], indent=2))
    if report["horizon_db"]:
        print("horizon stats:", json.dumps(report["horizon_db"], indent=2))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = Path(argv[1])
    report = build_report(out)
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
