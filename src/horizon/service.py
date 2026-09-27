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
from horizon.decision.layer import Option, evaluate
from horizon.decision.scorers import Scorer
from horizon.taskstate import rollback
from horizon.taskstate.judge import judge, targets_from_goal
from horizon.taskstate.store import TaskStore

log = logging.getLogger(__name__)

RECENT_PROGRESS = 5
RECENT_DECISIONS = 10


def redact_conditions(conditions: list[Condition] | None) -> list[Condition] | None:
    if conditions is None:
        return None
    return [c.model_copy(update={"value": redact(c.value, MAX_CONDITION_VALUE)}) if isinstance(c.value, str) else c
            for c in conditions]


def same_option(a: str | None, b: str | None) -> bool:
    """Whether the host implemented the option that was evaluated (labels get reworded a little)."""
    if not a or not b:
        return False
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or a in b or b in a


class ToolInputError(ValueError):
    """A problem with the host's input; the message tells the host what to do."""


def compact_task_state(task: TaskState, max_attempts: int) -> dict[str, Any]:
    """The always-included task state: small enough to send on every recall."""
    out = {
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
        "baseline": {"status": task.baseline, "failing_at_start": len(task.baseline_failing),
                     "target_tests": task.target_tests},
    }
    retry = rollback.retry_state(task, max_attempts)
    if retry:
        out["retry"] = retry  # the failure reasons, fed back into the decision step (PROJECT.md §8)
    return out


class Platform:
    def __init__(
        self,
        settings: Settings,
        tasks: TaskStore,
        memrouter_factory: Callable[[], MemRouter],
        cwd: str | None = None,
        scorer_factory: Callable[[], Scorer | None] = lambda: None,
    ):
        self.settings = settings
        self.tasks = tasks
        self._memrouter_factory = memrouter_factory
        self._memrouter: MemRouter | None = None
        self._scorer_factory = scorer_factory
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
        target_tests: list[str] | None = None,
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
                target_tests=list(dict.fromkeys((redact_list(target_tests, MAX_OPTION) or [])
                                                + targets_from_goal(goal))),
            ))
            return {
                "task_id": task.id,
                "task_state": self._compact(task),
                "next": ("Run the project's full test suite once now, before any edit, so Horizon records which "
                         "tests already fail (they won't count against you). Then call recall_context with this "
                         "task_id before your first decision or edit."),
            }

        return self._logged("start_task", None, run)

    def _compact(self, task: TaskState) -> dict[str, Any]:
        return compact_task_state(task, self.settings.max_attempts)

    def recall_context(
        self,
        task_id: str,
        situation: str,
        conditions: list[Condition] | None = None,
        token_budget: int | None = None,
        human_guidance: str | None = None,
    ) -> dict:
        situation = redact(situation, MAX_SITUATION)
        conditions = redact_conditions(conditions)
        human_guidance = redact(human_guidance, MAX_NOTE)

        def run() -> dict:
            task = self._task(task_id)
            if human_guidance:
                task.progress.append(ProgressEntry(note=f"Human guidance: {human_guidance}"))
                if task.status == "escalated":
                    rollback.resume(task)
            baseline_changed = task.baseline == "pending"
            self._take_baseline(task)
            # The PreToolUse hook snapshots the project just before this call (PROJECT.md §8).
            ckpt = self.tasks.take_checkpoint(task.cwd, task.created_at, task.id) if task.cwd else None
            slice_, status = self._call_memory(lambda m: m.recall(
                team_id=self.settings.team_id,
                situation=situation,
                conditions=conditions,
                token_budget=token_budget,
                task_id=task_id,
                project_id=task.project_id,
            ))
            if ckpt:
                ckpt.recall_id = slice_.recall_id if slice_ is not None else None
                rollback.attach_checkpoint(task, ckpt)
            if ckpt or human_guidance or task.restore_check or baseline_changed:
                task.restore_check = None  # this recall went through: the hook's restore check is done
                self.tasks.save(task)
            out: dict[str, Any] = {
                "task_id": task_id,
                "task_state": self._compact(task),
                "memory_status": status,
                "recall_id": None,
                "checkpoint_id": ckpt.id if ckpt else None,
                "memories": [],
            }
            if slice_ is not None:
                out["recall_id"] = slice_.recall_id
                out["memories"] = [m.model_dump(mode="json") for m in slice_.memories]
                out["memories_truncated"] = slice_.truncated
            if task.status == "escalated":
                out["next"] = ("This task is escalated after repeated failures: ask the user how to proceed "
                               "and call recall_context again with their answer as human_guidance.")
                return out
            out["next"] = ("After implementing and running tests, call record_outcome with this recall_id."
                           if slice_ is not None else
                           "Memory is unavailable; continue on the task state and still call record_outcome.")
            return out

        return self._logged("recall_context", task_id, run)

    def evaluate_options(
        self,
        task_id: str,
        situation: str,
        options: list[Option],
        conditions: list[Condition] | None = None,
        crucial: bool | None = None,
    ) -> dict:
        """Decision layer (PROJECT.md §5): score the host's options before it commits to a crucial choice."""
        situation = redact(situation, MAX_SITUATION)
        conditions = redact_conditions(conditions)
        options = [Option(label=redact(o.label, MAX_OPTION), description=redact(o.description, MAX_NOTE) or "",
                          est_cost_usd=o.est_cost_usd, est_tokens=o.est_tokens, est_latency_ms=o.est_latency_ms)
                   for o in options]

        def run() -> dict:
            if not 1 <= len(options) <= 6:
                raise ToolInputError("Pass 2-4 options (at most 6), each with a short label.")
            task = self._task(task_id)
            # Memory feeds the scorer evidence: track records and fear warnings (§5, MEMROUTER §13).
            slice_, memory_status = self._call_memory(lambda m: m.recall(
                team_id=self.settings.team_id, situation=situation, conditions=conditions,
                token_budget=self.settings.recall_token_budget, task_id=task_id, project_id=task.project_id))
            memories = slice_.memories if slice_ is not None else []
            state = {
                "goal": task.goal,
                "constraints": task.constraints,
                "situation": situation,
                "conditions": [c.model_dump(mode="json") for c in conditions or []],
                "options": [{"label": o.label, "description": o.description} for o in options],
                "past_outcomes": [{"situation": m.situation, "chosen": m.chosen, "outcome": m.outcome,
                                   "severity": m.severity} for m in memories],
            }
            try:
                scorer = self._scorer_factory()
            except Exception as exc:  # e.g. no API key: rank on estimates, never block the host
                log.warning("scorer unavailable: %s", exc)
                scorer, broken = None, True
            else:
                broken = False
            result = evaluate(state, options, scorer, self.settings, crucial_hint=crucial,
                              fear_warnings=sum(m.severity == "severe" for m in memories))
            if broken:
                result["scorer_status"] = "unavailable"
            task.last_evaluation = {"situation": situation, "decision": result["decision"],
                                    "chosen": result["chosen"], "predicted_success": result["predicted_success"],
                                    "source": result["scorer"]}
            self.tasks.save(task)
            return {"task_id": task_id, "memory_status": memory_status, **result}

        return self._logged("evaluate_options", task_id, run)

    def _take_baseline(self, task: TaskState) -> int:
        """Test runs made since start_task and before the first recall_context (so before any decision or
        edit) are the baseline (PROJECT.md §9). Taken by whichever of recall_context and record_outcome
        comes first. Returns how many test runs it took (0 when already taken or there were none)."""
        if task.baseline != "pending":
            return 0
        captures = self.tasks.pending_captures(task.cwd, since=task.created_at) if task.cwd else []
        if captures and all(c.failing is not None for c in captures):
            task.baseline = "captured"
            task.baseline_failing = list(dict.fromkeys(t for c in captures for t in c.failing))
        else:
            task.baseline = "missing"  # no run, or a run whose failures weren't all identified
        if captures:
            self.tasks.consume_captures([c.id for c in captures], f"baseline:{task.id}")
        return len(captures)

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
        failure_reason: str | None = None,
        plan: list[str] | None = None,
        agent_id: str | None = None,
    ) -> dict:
        # Store decision summaries, never raw code (PROJECT.md §12).
        situation = redact(situation, MAX_SITUATION)
        chosen = redact(chosen, MAX_OPTION)
        alternatives = redact_list(alternatives, MAX_OPTION)
        reason = redact(reason, MAX_NOTE)
        progress_note = redact(progress_note, MAX_NOTE)
        open_issues = redact_list(open_issues, MAX_NOTE)
        conditions = redact_conditions(conditions)
        failure_reason = redact(failure_reason, MAX_NOTE)
        plan = redact_list(plan, MAX_NOTE)

        def run() -> dict:
            task = self._task(task_id)
            if self._take_baseline(task):
                # The host recorded its pre-change test run: that run is the baseline, not an outcome.
                self.tasks.save(task)
                return {
                    "task_id": task.id,
                    "recorded": False,
                    "baseline": {"status": task.baseline, "failing_at_start": len(task.baseline_failing)},
                    "next": ("That test run was before any change, so it is the task's baseline: tests failing now "
                             "won't count against you. Call recall_context, make your change, run the tests, then "
                             "call record_outcome."),
                }
            tests, signal, captures = self._resolve_tests(task, tests_passed, tests_failed, signal_type)
            if tests is not None:
                actual_success = tests.pass_rate
            elif success is not None:
                actual_success = success
            else:
                raise ToolInputError(
                    "Provide tests_passed and tests_failed from your test run (preferred), or success (0..1)."
                )
            # Judge on regressions against the baseline, not the absolute pass rate (PROJECT.md §9).
            verdict = judge(task, tests, captures[0].failing if captures else None, actual_success,
                            self.settings.rollback_below)
            actual_success = verdict.success
            actual = Actual(success=actual_success, tokens=tokens, cost_usd=cost_usd, latency_ms=latency_ms,
                            test_results=tests, signal_type=signal)
            source, p_success = "host", predicted_success
            ev = task.last_evaluation
            if p_success is None and ev and ev.get("predicted_success") is not None and ev.get("source") \
                    and same_option(ev.get("chosen"), chosen):
                source, p_success = ev["source"], ev["predicted_success"]  # what the scorer predicted (§9)
            predicted = Predicted(success=p_success, tokens=predicted_tokens, cost_usd=predicted_cost_usd,
                                  latency_ms=predicted_latency_ms, source=source)

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
                agent_id=agent_id,
            ))
            episode_id = recorded.episode.id if recorded else None

            # Task state is updated whether or not memory worked.
            ckpt = rollback.checkpoint_for_decision(task, recall_id)
            task.decisions.append(DecisionEntry(situation=situation, chosen=chosen, reason=reason,
                                                episode_id=episode_id, success=round(actual_success, 3),
                                                checkpoint_id=ckpt.id if ckpt else None))
            summary = (f"tests {tests.passed}/{tests.total} passed" if tests
                       else f"success {actual_success:.0%}")
            if verdict.pre_existing:
                summary += f" ({len(verdict.pre_existing)} already failing at start)"
            if verdict.regressions:
                summary += f"; regressions: {', '.join(verdict.regressions[:5])}"
            task.progress.append(ProgressEntry(note=progress_note or f"{chosen}: {summary}"))
            if open_issues is not None:
                task.open_issues = open_issues
            if plan is not None:
                task.plan = plan
            failed = verdict.failed and not task_complete
            action = rollback.apply_outcome(task, failed=failed, chosen=chosen, reason=failure_reason or summary,
                                            checkpoint=ckpt, max_attempts=self.settings.max_attempts)
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
                "checkpoint_id": ckpt.id if ckpt else None,
                "test_judgement": verdict.report(),
                "rollback": action,
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
