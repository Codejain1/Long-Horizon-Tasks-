"""The platform service behind the MCP tools.

Task state is the primary path. Memrouter calls are isolated: if memrouter
fails, tools still work on task state and report ``memory_status``
(MEMROUTER.md §11).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from horizon.config import Settings
from horizon.memrouter.router import MemRouter
from horizon.models import (
    Actual,
    Condition,
    DecisionEntry,
    Predicted,
    ProgressEntry,
    Severity,
    SignalType,
    TaskState,
    TestResults,
)
from horizon.redact import MAX_CONDITION_VALUE, MAX_NOTE, MAX_OPTION, MAX_SITUATION, redact, redact_list
from horizon.taskstate.store import TaskStore

log = logging.getLogger(__name__)

RECENT_PROGRESS = 5
RECENT_DECISIONS = 10


def redact_conditions(conditions: list[Condition] | None) -> list[Condition] | None:
    if conditions is None:
        return None
    return [c.model_copy(update={"value": redact(c.value, MAX_CONDITION_VALUE)}) if isinstance(c.value, str) else c
            for c in conditions]


class ToolInputError(ValueError):
    """A problem with the host's input; the message tells the host what to do."""


def compact_task_state(task: TaskState) -> dict[str, Any]:
    """The always-included task state: small enough to send on every recall."""
    return {
        "task_id": task.id,
        "status": task.status,
        "goal": task.goal,
        "constraints": task.constraints,
        "plan": task.plan,
        "recent_progress": [p.note for p in task.progress[-RECENT_PROGRESS:]],
        "decisions": [
            {"situation": d.situation, "chosen": d.chosen, "reason": d.reason, "success": d.success}
            for d in task.decisions[-RECENT_DECISIONS:]
        ],
        "open_issues": task.open_issues,
    }


class Platform:
    def __init__(
        self,
        settings: Settings,
        tasks: TaskStore,
        memrouter_factory: Callable[[], MemRouter],
        cwd: str | None = None,
    ):
        self.settings = settings
        self.tasks = tasks
        self._memrouter_factory = memrouter_factory
        self._memrouter: MemRouter | None = None
        self.cwd = os.path.realpath(cwd or os.getcwd())

    # --- memrouter access, failure-isolated -----------------------------------

    def _memory(self) -> MemRouter:
        if self._memrouter is None:
            self._memrouter = self._memrouter_factory()  # retried on every call until it works
        return self._memrouter

    def _call_memory(self, fn: Callable[[MemRouter], Any]) -> tuple[Any, str]:
        try:
            return fn(self._memory()), "ok"
        except Exception as exc:  # memrouter must never take task state down with it
            log.warning("memrouter unavailable: %s", exc)
            return None, "unavailable"

    def _task(self, task_id: str) -> TaskState:
        task = self.tasks.get(task_id, self.settings.team_id)
        if task is None:
            raise ToolInputError(f"Unknown task_id {task_id!r}. Call start_task first and use the task_id it returns.")
        return task

    def _logged(self, tool: str, task_id: str | None, fn: Callable[[], dict]) -> dict:
        try:
            result = fn()
        except Exception as exc:
            self.tasks.log_call(tool, task_id, ok=False, error=str(exc)[:500])
            raise
        self.tasks.log_call(tool, task_id or result.get("task_id"), ok=True)
        return result

    # --- tools -----------------------------------------------------------------

    def start_task(
        self,
        goal: str,
        constraints: list[str] | None = None,
        plan: list[str] | None = None,
        open_issues: list[str] | None = None,
        project_id: str | None = None,
    ) -> dict:
        def run() -> dict:
            if not goal.strip():
                raise ToolInputError("goal must not be empty; pass the user's request verbatim.")
            task = self.tasks.create(TaskState(
                team_id=self.settings.team_id,
                project_id=project_id,
                goal=goal,  # verbatim (PROJECT.md §8); everything else is redacted
                constraints=redact_list(constraints, MAX_NOTE) or [],
                plan=redact_list(plan, MAX_NOTE) or [],
                open_issues=redact_list(open_issues, MAX_NOTE) or [],
                cwd=self.cwd,
            ))
            return {
                "task_id": task.id,
                "task_state": compact_task_state(task),
                "next": "Call recall_context with this task_id before your first decision or edit.",
            }

        return self._logged("start_task", None, run)

    def recall_context(
        self,
        task_id: str,
        situation: str,
        conditions: list[Condition] | None = None,
        token_budget: int | None = None,
    ) -> dict:
        situation = redact(situation, MAX_SITUATION)
        conditions = redact_conditions(conditions)

        def run() -> dict:
            task = self._task(task_id)
            slice_, status = self._call_memory(lambda m: m.recall(
                team_id=self.settings.team_id,
                situation=situation,
                conditions=conditions,
                token_budget=token_budget,
                task_id=task_id,
            ))
            out: dict[str, Any] = {
                "task_id": task_id,
                "task_state": compact_task_state(task),
                "memory_status": status,
                "recall_id": None,
                "memories": [],
            }
            if slice_ is not None:
                out["recall_id"] = slice_.recall_id
                out["memories"] = [m.model_dump(mode="json") for m in slice_.memories]
                out["memories_truncated"] = slice_.truncated
            out["next"] = ("After implementing and running tests, call record_outcome with this recall_id."
                           if slice_ is not None else
                           "Memory is unavailable; continue on the task state and still call record_outcome.")
            return out

        return self._logged("recall_context", task_id, run)

    def record_outcome(
        self,
        task_id: str,
        situation: str,
        chosen: str,
        success: float | None = None,
        tests_passed: int | None = None,
        tests_failed: int | None = None,
        alternatives: list[str] | None = None,
        conditions: list[Condition] | None = None,
        reason: str | None = None,
        predicted_success: float | None = None,
        predicted_tokens: float | None = None,
        predicted_cost_usd: float | None = None,
        predicted_latency_ms: float | None = None,
        tokens: float | None = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        signal_type: SignalType = "auto",
        severity: Severity = "normal",
        recall_id: str | None = None,
        progress_note: str | None = None,
        open_issues: list[str] | None = None,
        task_complete: bool = False,
    ) -> dict:
        # Store decision summaries, never raw code (PROJECT.md §12).
        situation = redact(situation, MAX_SITUATION)
        chosen = redact(chosen, MAX_OPTION)
        alternatives = redact_list(alternatives, MAX_OPTION)
        reason = redact(reason, MAX_NOTE)
        progress_note = redact(progress_note, MAX_NOTE)
        open_issues = redact_list(open_issues, MAX_NOTE)
        conditions = redact_conditions(conditions)

        def run() -> dict:
            task = self._task(task_id)
            tests, signal, captures = self._resolve_tests(task, tests_passed, tests_failed, signal_type)
            if tests is not None:
                actual_success = tests.pass_rate
            elif success is not None:
                actual_success = success
            else:
                raise ToolInputError(
                    "Provide tests_passed and tests_failed from your test run (preferred), or success (0..1)."
                )
            actual = Actual(success=actual_success, tokens=tokens, cost_usd=cost_usd, latency_ms=latency_ms,
                            test_results=tests, signal_type=signal)
            predicted = Predicted(success=predicted_success, tokens=predicted_tokens,
                                  cost_usd=predicted_cost_usd, latency_ms=predicted_latency_ms)

            recorded, status = self._call_memory(lambda m: m.record(
                team_id=self.settings.team_id,
                situation=situation,
                chosen=chosen,
                actual=actual,
                predicted=predicted,
                alternatives=alternatives,
                conditions=conditions,
                project_id=task.project_id,
                task_id=task.id,
                recall_id=recall_id,
                severity=severity,
            ))
            episode_id = recorded.episode.id if recorded else None

            # Task state is updated whether or not memory worked.
            task.decisions.append(DecisionEntry(situation=situation, chosen=chosen, reason=reason,
                                                episode_id=episode_id, success=round(actual_success, 3)))
            summary = (f"tests {tests.passed}/{tests.total} passed" if tests
                       else f"success {actual_success:.0%}")
            task.progress.append(ProgressEntry(note=progress_note or f"{chosen}: {summary}"))
            if open_issues is not None:
                task.open_issues = open_issues
            if task_complete:
                task.status = "completed"
            self.tasks.save(task)
            if captures:
                self.tasks.consume_captures([c.id for c in captures], episode_id or f"task:{task.id}")

            out: dict[str, Any] = {
                "task_id": task.id,
                "episode_id": episode_id,
                "memory_status": status,
                "actual_success": round(actual_success, 3),
                "test_results_source": tests.source if tests else None,
                "task_status": task.status,
            }
            if recorded:
                out.update(surprise=round(recorded.surprise.surprise, 3),
                           low_confidence=recorded.episode.low_confidence)
            return out

        return self._logged("record_outcome", task_id, run)

    def _resolve_tests(
        self, task: TaskState, passed: int | None, failed: int | None, signal: SignalType
    ) -> tuple[TestResults | None, SignalType, list]:
        """Prefer real test counts captured by the hook over the host's own report (PROJECT.md §9)."""
        captures = self.tasks.pending_captures(task.cwd, since=task.created_at) if task.cwd else []
        if captures:
            latest = captures[0]
            return (TestResults(passed=latest.passed, failed=latest.failed, total=latest.total,
                                source="hook", runner=latest.runner), "auto", captures)
        if passed is not None or failed is not None:
            p, f = passed or 0, failed or 0
            if p + f == 0:
                return None, signal, []
            return TestResults(passed=p, failed=f, total=p + f, source="host"), signal, []
        return None, signal, []
