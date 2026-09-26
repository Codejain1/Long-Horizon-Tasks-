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


def tool_calls(transcript: Path) -> list[dict]:
    """Tool calls in order: [{"name": ..., "input": {...}}]."""
    calls = []
    for line in transcript.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append({"name": block.get("name", ""), "input": block.get("input") or {}})
    return calls


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
    last_test = last(lambda c: c["name"] == "Bash" and TEST_CMD.search(str(c["input"].get("command", ""))))
    last_record = last(lambda c: c["name"] == HORIZON + "record_outcome")
    before_edit = (lambda i: i is not None and (first_edit is None or i < first_edit))
    return {
        "tool_calls": len(calls),
        "start_task": names.count(HORIZON + "start_task"),
        "recall_context": names.count(HORIZON + "recall_context"),
        "record_outcome": names.count(HORIZON + "record_outcome"),
        "started_before_edit": before_edit(start),
        "recalled_before_edit": before_edit(recall),
        "recorded_after_last_test": last_test is None or (last_record is not None and last_record > last_test),
        "ran_tests": last_test is not None,
        "task_complete_sent": any(c["name"] == HORIZON + "record_outcome" and c["input"].get("task_complete")
                                  for c in calls),
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


RATE_KEYS = ("started_before_edit", "recalled_before_edit", "recorded_after_last_test", "task_complete_sent")


def build_report(out: Path) -> dict:
    tasks = {}
    for transcript in sorted((out / "logs").glob("*.jsonl")):
        task_id = transcript.stem
        row = analyse(tool_calls(transcript))
        row["tests_pass_after"] = check_passed(out / "logs" / f"{task_id}.check.txt")
        tasks[task_id] = row
    n = len(tasks)
    rates = {k: round(sum(bool(t[k]) for t in tasks.values()) / n, 2) for k in RATE_KEYS} if n else {}
    if n:
        rates["tasks_passing"] = round(sum(t["tests_pass_after"] is True for t in tasks.values()) / n, 2)
    return {"tasks": tasks, "rates": rates, "horizon_db": db_stats(out)}


def print_report(report: dict) -> None:
    cols = ("start_task", "recall_context", "record_outcome", "started_before_edit", "recalled_before_edit",
            "recorded_after_last_test", "tests_pass_after")
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
