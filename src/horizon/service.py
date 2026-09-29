"""The platform service behind the MCP tools.

Task state is the primary path. Memrouter calls are isolated: if memrouter
fails, tools still work on task state and report ``memory_status``
(MEMROUTER.md §11).
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from horizon.config import Settings
from horizon.memrouter.router import MemRouter, task_type
from horizon.memrouter.spikes import same_option
from horizon.models import (
    Actual,
    Checkpoint,
    Condition,
    DecisionEntry,
    GitRef,
    Predicted,
    ProgressEntry,
    Severity,
    SignalType,
    TaskState,
    TestResults,
    now,
)
from horizon.redact import MAX_CONDITION_VALUE, MAX_NOTE, MAX_OPTION, MAX_SITUATION, redact, redact_list
from horizon.decision.layer import Option, first_pass, plan_consequences, second_pass
from horizon.decision.log import DecisionLog, new_record
from horizon.decision.scorers import Scorer
from horizon.decision.worldmodel import evaluate as evaluate_world_model
from horizon.decision.worldmodel import fill_estimates, make_world_model
from horizon.taskstate import rollback
from horizon.taskstate.judge import judge, targets_from_goal
from horizon.taskstate.store import TaskStore

log = logging.getLogger(__name__)

RECENT_PROGRESS = 5
MIN_TRUST_SAMPLES = 20  # scored outcomes before a scorer's measured bias is corrected
RECENT_DECISIONS = 10


def redact_conditions(conditions: list[Condition] | None) -> list[Condition] | None:
    if conditions is None:
        return None
    return [c.model_copy(update={"value": redact(c.value, MAX_CONDITION_VALUE)}) if isinstance(c.value, str) else c
            for c in conditions]


class ToolInputError(ValueError):
    """A problem with the host's input; the message tells the host what to do."""


def _pass_log(n: int, result: dict) -> dict:
    """One scoring pass as stored in the world-model log: raw answers plus the decision it led to."""
    return {"pass": n, "answers": result.get("answers"),
            "result": {k: v for k, v in result.items() if k != "answers"}}


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
        self._world_model = None
        self._gate: dict[str, tuple[float, dict]] = {}  # team -> (when, gate result)
        self.decisions = DecisionLog(tasks.db)
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

    # --- world model (PROJECT.md §6), failure-isolated like memrouter ------------------------------

    def _world(self):
        if self._world_model is None:
            self._world_model = make_world_model(self.settings, self._memory())
        return self._world_model

    def world_model_gate(self, refresh: bool = False) -> dict:
        """Whether the world model beats the stand-ins on replayed outcomes; reused for a while (the replay is
        the expensive part)."""
        team = self.settings.team_id
        cached = self._gate.get(team)
        if cached and not refresh and time.monotonic() - cached[0] < self.settings.world_model_gate_ttl_s:
            return cached[1]
        result = evaluate_world_model(self._world(), team, self.settings)
        self._gate[team] = (time.monotonic(), result)
        return result

    def _forecast(self, situation: str, conditions: list[Condition] | None, options: list[Option]) -> dict | None:
        """The world model's forecast per option, logged with the decision. `active` = it may be used."""
        mode = self.settings.world_model
        if mode == "off":
            return None
        try:
            started = time.monotonic()
            forecasts = self._world().predict(self.settings.team_id, situation, conditions,
                                              [o.label for o in options])
            ms = round((time.monotonic() - started) * 1000, 1)
            active = mode == "on" or (mode == "auto" and self.world_model_gate()["passed"])
        except Exception as exc:  # a world model failure never blocks a decision
            log.warning("world model unavailable: %s", exc)
            return {"mode": mode, "status": "unavailable", "active": False}
        return {"model": self._world_model.name, "mode": mode, "status": "ok", "active": active, "ms": ms,
                "forecasts": {label: f.as_dict() for label, f in forecasts.items()}}

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
        cwd: str | None = None,
    ) -> dict:
        """`cwd` identifies the project for hook matching: the hosted server gets the client's hashed project
        key from a header; locally it's this server's working directory."""

        def run() -> dict:
            if not goal.strip():
                raise ToolInputError("goal must not be empty; pass the user's request verbatim.")
            task = self.tasks.create(TaskState(
                team_id=self.settings.team_id,
                project_id=(project_id or "").strip()[:100] or None,
                goal=goal,  # verbatim (PROJECT.md §8); everything else is redacted
                constraints=redact_list(constraints, MAX_NOTE) or [],
                plan=redact_list(plan, MAX_NOTE) or [],
                open_issues=redact_list(open_issues, MAX_NOTE) or [],
                cwd=cwd or self.cwd,
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
        checkpoint_commit: str | None = None,
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
            if ckpt is None and checkpoint_commit:  # hosts without hooks report their own git commit
                if not re.fullmatch(r"[0-9a-f]{7,40}", checkpoint_commit):
                    raise ToolInputError("checkpoint_commit must be a git commit id (7-40 hex characters).")
                ckpt = Checkpoint(cwd=task.cwd or "", git=GitRef(repo=".", commit=checkpoint_commit))
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
                out["fear_warnings"] = slice_.fear_warnings
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
        """Decision layer (PROJECT.md §5-6): score the host's options before it commits to a crucial choice.
        A close call returns a consequence plan; the host runs it and calls submit_consequences."""
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
            fear = sum(m.severity == "severe" for m in memories)
            state = {
                "goal": task.goal,
                "constraints": task.constraints,
                "situation": situation,
                "conditions": [c.model_dump(mode="json") for c in conditions or []],
                "options": [{"label": o.label, "description": o.description} for o in options],
                "past_outcomes": [{"situation": m.situation, "chosen": m.chosen, "outcome": m.outcome,
                                   "severity": m.severity} for m in memories],
            }
            scorer, broken = self._scorer()
            bias = self._scorer_bias(scorer)
            # The world model forecasts every option (logged for training and the gate); once active, its
            # forecasts stand in for missing estimates and can settle a close call instead of spikes (§6).
            world = self._forecast(situation, conditions, options)
            active = bool(world and world["active"])
            opts = options
            if active:
                opts, world["filled_estimates"] = fill_estimates(options, world["forecasts"])
            first = first_pass(state, opts, scorer, self.settings, crucial_hint=crucial, fear_warnings=fear, bias=bias)
            if broken:
                first["scorer_status"] = "unavailable"
            record = new_record(team_id=self.settings.team_id, project_id=task.project_id, task_id=task.id,
                                state=state, options=[asdict(o) for o in opts],
                                scorer={"name": first["scorer"], "status": first["scorer_status"]})
            record["world_model"] = world
            record["passes"].append(_pass_log(1, first))

            result = first
            if first["decision"] == "close":
                past, _ = self._call_memory(lambda m: m.lookup_simulation(
                    team_id=self.settings.team_id, situation=situation, options=first["close"]))
                plan = plan_consequences(first, situation, past or {}, self.settings,
                                         can_roll_back=any(c.git for c in task.checkpoints),
                                         forecasts=world["forecasts"] if active else None)
                record["consequences"] = {"plan": plan, "submitted": None}
                if plan["mode"] in ("memory", "world_model"):
                    # Every close option was tested before, or the rest are confidently forecast: no spikes.
                    evidence = {label: p["result"] for label, p in plan["from_memory"].items()}
                    evidence.update({label: {"tested": False, "world_model": f}
                                     for label, f in (plan.get("forecasts") or {}).items()})
                    stage = "from past spike results" if plan["mode"] == "memory" else "from the world model's forecasts"
                    result = second_pass(state, opts, evidence, scorer, self.settings, first,
                                         fear_warnings=fear, stage=stage, bias=bias)
                    record["passes"].append(_pass_log(2, result))
                    result["settled_by"] = plan["mode"]
                    result["spike_tokens_avoided"] = int(sum(
                        ((p["result"].get("spike") or {}).get("metrics") or {}).get("tokens", 0)
                        for p in plan["from_memory"].values()))
                elif plan["mode"] == "try_and_rollback":
                    top = plan["order"][0]
                    result = {**first, "decision": "try_and_rollback", "chosen": top,
                              "predicted_success": next(r["dimensions"]["success"] for r in first["options"]
                                                        if r["label"] == top),
                              "try_order": plan["order"],
                              "reason": "Close call, and every close option is cheap to undo: trying is cheaper "
                                        "than simulating (PROJECT.md §6 step 5).",
                              "next": (f"Implement {top} and run the tests. If they regress, the rollback rules "
                                       f"restore the checkpoint: then try {plan['order'][1]}.")}
                else:
                    result = {**first, "decision": "check_consequences", "consequence_plan": plan,
                              "reason": "Close call: check the consequences before choosing (PROJECT.md §6).",
                              "next": ("Run the static_checks, then one spike per option in consequence_plan.spikes "
                                       "(never in the working tree). Skip an option's spike if its static checks "
                                       "fail. Then call submit_consequences with the decision_id and the results.")}
            record["stage"] = "awaiting_consequences" if result["decision"] == "check_consequences" else "decided"
            return {**self._finish_decision(task, record, result), "memory_status": memory_status}

        return self._logged("evaluate_options", task_id, run)

    def submit_consequences(self, task_id: str, decision_id: str, results: list[dict]) -> dict:
        """Second scoring pass with the host's structured consequence results as evidence (PROJECT.md §6)."""

        # Structured results only (§6): notes are redacted and short, metrics are numbers with short names.
        results = [{**r, "notes": redact(r.get("notes"), MAX_NOTE),
                    "spike": None if r.get("spike") is None else {
                        **r["spike"], "metrics": {k[:40]: v for k, v in (r["spike"].get("metrics") or {}).items()}},
                    "static_checks": [{"name": redact(c["name"], MAX_OPTION), "passed": c["passed"]}
                                      for c in r.get("static_checks") or []]}
                   for r in results]

        def run() -> dict:
            task = self._task(task_id)
            record = self.decisions.get(decision_id, self.settings.team_id)
            if record is None or record["task_id"] != task.id:
                raise ToolInputError(f"Unknown decision_id {decision_id!r} for this task. Use the decision_id "
                                     "from evaluate_options.")
            if record["stage"] != "awaiting_consequences":
                raise ToolInputError("This decision is already made; submit_consequences runs once per close call. "
                                     "Implement the chosen option, or call evaluate_options for a new decision.")
            first = record["passes"][0]["result"]
            evidence = {}
            for r in results:
                # Labels were redacted when stored; compare the host's label the same way.
                label = next((c for c in first["close"] if same_option(c, r["option"])
                              or same_option(c, redact(r["option"], MAX_OPTION))), None)
                if label is None:
                    raise ToolInputError(f"{r['option']!r} is not one of the close options: {first['close']}.")
                evidence[label] = r
            # Reuse what memory already had for options the host didn't test again.
            for label, past in record["consequences"]["plan"]["from_memory"].items():
                evidence.setdefault(label, past["result"])
            options = [Option(**o) for o in record["options"]]
            scorer, broken = self._scorer()
            fear = sum(m["severity"] == "severe" for m in record["state"]["past_outcomes"])
            result = second_pass(record["state"], options, evidence, scorer, self.settings, first,
                                 fear_warnings=fear, bias=self._scorer_bias(scorer))
            if broken:
                result["scorer_status"] = "unavailable"
            result["settled_by"] = "consequences"
            record["passes"].append(_pass_log(2, result))
            record["consequences"]["submitted"] = results
            record["stage"] = "decided"
            for r in results:  # spike results become memory for the next similar tie (MEMROUTER §13)
                if (r.get("spike") or {}).get("ran"):
                    self._call_memory(lambda m, r=r: m.record_spike(
                        team_id=self.settings.team_id, situation=record["state"]["situation"],
                        option=r["option"], result=r, project_id=task.project_id))
            return self._finish_decision(task, record, result)

        return self._logged("submit_consequences", task_id, run)

    def _log_outcome(self, task: TaskState, chosen: str, episode_id: str | None, actual: Actual, verdict,
                     recorded) -> None:
        """Attach the outcome to the decision it implemented: the world-model training target (§6)."""
        ev = task.last_evaluation
        if not ev or not ev.get("decision_id"):
            return
        implemented = next((o for o in ev.get("options", []) if same_option(o, chosen)), None)
        if implemented is None:
            return  # a later, unrelated outcome
        record = self.decisions.get(ev["decision_id"], self.settings.team_id)
        if record is None:
            return
        record["outcomes"].append({
            "option": implemented, "followed_decision": same_option(ev.get("chosen"), chosen),
            "episode_id": episode_id, "recorded_at": now().isoformat(),
            "success": actual.success, "tokens": actual.tokens, "cost_usd": actual.cost_usd,
            "latency_ms": actual.latency_ms, "signal_type": actual.signal_type,
            "tests": actual.test_results.model_dump(mode="json") if actual.test_results else None,
            "judgement": verdict.report(), "surprise": recorded.surprise.surprise if recorded else None,
        })
        self.decisions.save(record)
        # Predictor trust for the world model (MEMROUTER §5 step 4), from what it forecast before the outcome.
        # A forecast with no real evidence behind it is just the base rate, so it isn't scored.
        forecast = ((record.get("world_model") or {}).get("forecasts") or {}).get(implemented) or {}
        if forecast.get("samples", 0) >= 1 and forecast.get("success") is not None:
            kind = task_type([Condition(**c) for c in record["state"].get("conditions") or []])
            self._call_memory(lambda m: m.graph.update_predictor(self.settings.team_id, "world_model", kind,
                                                                 forecast["success"], actual.success))

    def _scorer_bias(self, scorer: Scorer | None) -> float:
        """The scorer's measured bias (mean predicted - mean actual success), once it has enough outcomes."""
        if scorer is None:
            return 0.0
        stats, _ = self._call_memory(lambda m: m.predictor_trust(self.settings.team_id, scorer.name))
        samples = sum(s["samples"] for s in stats or [])
        if samples < MIN_TRUST_SAMPLES:
            return 0.0
        return max(-0.5, min(0.5, sum(s["calibration"] * s["samples"] for s in stats) / samples))

    def _scorer(self) -> tuple[Scorer | None, bool]:
        try:
            return self._scorer_factory(), False
        except Exception as exc:  # e.g. no API key: rank on estimates, never block the host
            log.warning("scorer unavailable: %s", exc)
            return None, True

    def _finish_decision(self, task: TaskState, record: dict, result: dict) -> dict:
        result = {k: v for k, v in result.items() if k != "answers"}  # raw answers go to the log, not the host
        if record["stage"] == "decided":
            record["final"] = {k: result.get(k) for k in ("decision", "chosen", "predicted_success", "reason",
                                                          "settled_by", "margin")}
        self.decisions.save(record)
        task.last_evaluation = {"decision_id": record["id"], "situation": record["state"]["situation"],
                                "decision": result["decision"], "chosen": result["chosen"],
                                "options": [o["label"] for o in record["options"]],
                                # The raw scorer prediction is recorded, so predictor trust measures the scorer.
                                "predicted_success": result.get("predicted_success_raw", result["predicted_success"]),
                                "source": result["scorer"]}
        self.tasks.save(task)
        return {"task_id": task.id, "decision_id": record["id"], **result}

    # --- inspection tools (PROJECT.md §10-11) ------------------------------------------

    def explain_decision(self, task_id: str, decision_id: str | None = None) -> dict:
        """Why a decision went the way it did: evidence, scores per pass, consequences, and what happened."""

        def run() -> dict:
            task = self._task(task_id)
            did = decision_id or (task.last_evaluation or {}).get("decision_id")
            record = self.decisions.get(did, self.settings.team_id) if did else None
            if record is None or record["task_id"] != task.id:
                raise ToolInputError("No such decision for this task. Pass a decision_id from evaluate_options, or "
                                     "omit it to explain the task's latest decision.")
            passes = [p["result"] for p in record["passes"]]
            last, first = passes[-1], passes[0]
            eliminated = set(last.get("eliminated") or [])
            plan = (record["consequences"] or {}).get("plan") or {}
            submitted = (record["consequences"] or {}).get("submitted") or []
            return {
                "task_id": task.id,
                "decision_id": record["id"],
                "situation": record["state"]["situation"],
                "stage": record["stage"],
                "final": record["final"],
                "scorer": record["scorer"],
                "crucial_signals": first.get("crucial_signals"),
                "high_stakes": first.get("high_stakes"),
                "passes": [{"pass": p["pass"], "decision": p["result"]["decision"],
                            "margin": p["result"].get("margin")} for p in record["passes"]],
                "options": [{**{k: o[k] for k in ("label", "composite", "confidence", "dimensions")},
                             "eliminated": o["label"] in eliminated} for o in last["options"]],
                "evidence": {"past_outcomes": record["state"]["past_outcomes"],
                             "consequence_mode": plan.get("mode"),
                             "reused_from_memory": sorted(plan.get("from_memory") or {}),
                             "checks": [{"option": r["option"],
                                         "static_checks_failed": sum(c["passed"] is False for c in r["static_checks"]),
                                         "spike": r.get("spike")} for r in submitted]},
                "outcomes": record["outcomes"],
            }

        return self._logged("explain_decision", task_id, run)

    def show_memories(self, query: str | None = None, kinds: list[str] | None = None, limit: int = 20) -> dict:
        query = redact(query, MAX_SITUATION)

        def run() -> dict:
            memories, status = self._call_memory(lambda m: m.inspect(self.settings.team_id, query, kinds, limit))
            return {"memory_status": status, "memories": memories or []}

        return self._logged("show_memories", None, run)

    def delete_memory(self, memory_id: str, reason: str, removed_by: str | None = None) -> dict:
        reason = redact(reason, MAX_NOTE)

        def run() -> dict:
            try:
                return self._memory().remove(self.settings.team_id, memory_id, reason, removed_by)
            except ValueError as exc:
                raise ToolInputError(str(exc)) from exc

        return self._logged("delete_memory", None, run)

    def clear_fear(self, lesson_id: str, by_human: str) -> dict:
        """Human only (MEMROUTER §9, §12): the MCP tool asks the user through elicitation first."""

        def run() -> dict:
            try:
                les = self._memory().clear_fear(self.settings.team_id, lesson_id, by_human)
            except ValueError as exc:
                raise ToolInputError(str(exc)) from exc
            return {"lesson_id": les.id, "statement": les.statement, "cleared_by": les.cleared_by}

        return self._logged("clear_fear", None, run)

    # --- human approvals (PROJECT.md §10) --------------------------------------------------

    def log_approval(self, task_id: str | None, kind: str, action: str, question: str,
                     answer: str | None = None, approver: str | None = None) -> None:
        self.tasks.log_approval(self.settings.team_id, task_id, kind, action, question, answer, approver)

    def human_choice(self, task_id: str, decision_id: str, choice: str, approver: str | None) -> dict:
        """A human picked the option for a high-stakes close call: that is the decision."""
        task = self._task(task_id)
        record = self.decisions.get(decision_id, self.settings.team_id)
        if record is None or record["task_id"] != task.id:
            raise ToolInputError(f"Unknown decision_id {decision_id!r}.")
        rows = record["passes"][-1]["result"]["options"]
        row = next((r for r in rows if same_option(r["label"], choice)), None)
        if row is None:
            raise ToolInputError(f"{choice!r} is not one of the options.")
        record["final"] = {**(record["final"] or {}), "decision": "human_choice", "chosen": row["label"],
                           "predicted_success": row["dimensions"].get("success"), "approver": approver,
                           "reason": f"Chosen by {approver or 'the user'} (high stakes)."}
        record["stage"] = "decided"
        self.decisions.save(record)
        task.last_evaluation = {**(task.last_evaluation or {}), "decision_id": record["id"],
                                "decision": "human_choice", "chosen": row["label"],
                                "predicted_success": row["dimensions"].get("success")}
        self.tasks.save(task)
        return record["final"]

    def resume_with_guidance(self, task_id: str, guidance: str) -> dict:
        """The human answered an escalation (Phase 3): resume the task with their guidance."""
        guidance = redact(guidance, MAX_NOTE)
        task = self._task(task_id)
        task.progress.append(ProgressEntry(note=f"Human guidance: {guidance}"))
        if task.status == "escalated":
            rollback.resume(task)
        self.tasks.save(task)
        return self._compact(task)

    def _baseline_echo(self, task: TaskState, passed: int | None, failed: int | None) -> bool:
        """A first record_outcome reporting test counts when hooks saw no test run since the baseline: the host
        is recording its baseline run (seen with Codex), not the outcome of a change."""
        if task.baseline != "captured" or task.decisions or (passed is None and failed is None):
            return False
        return not (self.tasks.pending_captures(task.cwd, since=task.created_at) if task.cwd else [])

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
        subagent: str | None = None,
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
            if self._take_baseline(task) or self._baseline_echo(task, tests_passed, tests_failed):
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
            # What the attempt really cost, from the host's transcript (the PreToolUse hook): the host can't
            # know its own token usage, so without this the efficiency targets would stay empty.
            usage = self.tasks.take_usage(task.cwd, task.created_at, f"task:{task.id}") if task.cwd else None
            usage = usage or {}
            usage_used = bool(usage) and (tokens is None or latency_ms is None)
            actual = Actual(success=actual_success, tokens=usage.get("tokens") if tokens is None else tokens,
                            cost_usd=cost_usd,
                            latency_ms=usage.get("latency_ms") if latency_ms is None else latency_ms,
                            test_results=tests, signal_type=signal)
            source, p_success = "host", predicted_success
            ev = task.last_evaluation
            if ev and ev.get("predicted_success") is not None and ev.get("source") \
                    and same_option(ev.get("chosen"), chosen):
                # §9: record what the scorer predicted for the option it chose, over the host's own guess, so
                # predictor trust (Phase 6) measures Jev. Seen live: hosts pass their own predicted_success too.
                source, p_success = ev["source"], ev["predicted_success"]
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
                # Provenance (MEMROUTER §11): the host client, plus the subagent it names, so a bad memory can
                # be traced to the agent that wrote it.
                agent_id=":".join(x for x in (agent_id, redact(subagent, 60) if subagent else None) if x) or None,
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
            action = rollback.apply_outcome(task, failed=failed, severe=severity == "severe" and not task_complete,
                                            chosen=chosen, reason=failure_reason or summary,
                                            checkpoint=ckpt, max_attempts=self.settings.max_attempts)
            if task_complete:
                task.status = "completed"
            self.tasks.save(task)
            if captures:
                self.tasks.consume_captures([c.id for c in captures], episode_id or f"task:{task.id}")
            self._log_outcome(task, chosen, episode_id, actual, verdict, recorded)

            out: dict[str, Any] = {
                "task_id": task.id,
                "episode_id": episode_id,
                "memory_status": status,
                "actual_success": round(actual_success, 3),
                "test_results_source": tests.source if tests else None,
                "usage_source": "hook" if usage_used else None,
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
