"""The runner adapter (v2): Horizon's long-horizon runner (`deploy/longrun/`) as a domain, like coding.

The runner plans a goal into steps, does one step at a time with tools, and its watcher checks each step. Here its
work becomes Horizon events, so the general core (event log, transitions, stall detection, episodes, world model)
sees runner sessions the same way it sees Claude Code sessions:

- `plan` steps (the step list), `tool` steps (`run` / `write_file` / `read_file`), `check` steps (the watcher's
  verdict, with test counts parsed by `testparse`), `decision` steps (what the watcher chose and why).
- state: steps done and failed, checks, the last test counts, decisions taken.
- outcome: the last check's test pass rate when tests ran, else the share of planned steps done.
"""

from __future__ import annotations

from typing import Any

from horizon.adapters.base import Outcome, first_prompt
from horizon.adapters.coding import classify_command
from horizon.eventlog.schema import Action, Event, Result
from horizon.testparse import parse_test_output

DOMAIN = "runner"
SOURCE = "longrun"


def check_result(ok: bool, output: str, step_id: str, attempt: int) -> Result:
    """The watcher's verdict as a Result, with test counts when the check ran tests."""
    metrics: dict[str, float] = {"attempt": float(attempt)}
    facts: dict[str, Any] = {"step": step_id}
    counts = parse_test_output(output or "")
    if counts:
        metrics.update(passed=counts.passed, failed=counts.failed, total=counts.passed + counts.failed)
        if counts.failing:
            facts["failing"] = list(counts.failing)[:20]
    return Result(ok=ok, metrics=metrics, facts=facts)


def event(kind: str, *, team_id: str, project: str, session_id: str, text: str | None = None,
          action: Action | None = None, result: Result | None = None, data: dict | None = None) -> Event:
    return Event(team_id=team_id, project=project, session_id=session_id, domain=DOMAIN, source=SOURCE, kind=kind,
                 text=text, action=action, result=result, data=data or {})


class RunnerAdapter:
    domain = DOMAIN

    def initial_state(self) -> dict:
        return {"planned": 0, "plans": 0, "steps_done": [], "steps_failed": [], "checks": 0, "failed_checks": 0,
                "tests": None, "decisions": [], "commands": 0, "writes": 0}

    def reduce(self, state: dict, event: Event) -> dict:
        if event.kind != "step" or event.action is None:
            return state
        state = {**state, "steps_done": list(state["steps_done"]), "steps_failed": list(state["steps_failed"]),
                 "decisions": list(state["decisions"])}
        a, r = event.action, event.result or Result()
        if a.name == "plan":
            state["plans"] += 1
            state["planned"] = int(r.metrics.get("steps", 0))
        elif a.name == "run":
            state["commands"] += 1
        elif a.name == "write_file":
            state["writes"] += 1
        elif a.name == "check":
            state["checks"] += 1
            step = r.facts.get("step")
            if r.ok:
                if step and step not in state["steps_done"]:
                    state["steps_done"].append(step)
            else:
                state["failed_checks"] += 1
            if "total" in r.metrics:
                state["tests"] = {"passed": int(r.metrics["passed"]), "failed": int(r.metrics["failed"]),
                                  "total": int(r.metrics["total"]), "failing": r.facts.get("failing")}
        elif a.name == "step_failed" and a.target:
            state["steps_failed"].append(a.target)
        elif a.name == "decision":
            state["decisions"].append(a.target)
        return state

    def classify(self, action: str, raw: dict) -> tuple[bool, str | None]:
        if action == "run":
            return classify_command(str(raw.get("cmd") or ""))
        return False, None

    def outcome(self, events: list[Event]) -> Outcome | None:
        if not events:
            return None
        state = self.initial_state()
        for e in events:
            state = self.reduce(state, e)
        tests = state["tests"]
        if tests and tests["total"]:
            success = tests["passed"] / tests["total"]
        elif state["planned"]:
            success = len(state["steps_done"]) / state["planned"]
        else:
            success = None
        metrics = {"plans": state["plans"], "planned": state["planned"], "steps_done": len(state["steps_done"]),
                   "checks": state["checks"], "failed_checks": state["failed_checks"], "commands": state["commands"],
                   "writes": state["writes"], "decisions": len(state["decisions"])}
        facts: dict[str, Any] = {"decisions": state["decisions"][-20:]}
        if tests:
            facts["tests"] = {k: tests[k] for k in ("passed", "failed", "total")}
        return Outcome(success=success, metrics=metrics, facts=facts)

    def situation(self, events: list[Event]) -> str | None:
        return first_prompt(events)
