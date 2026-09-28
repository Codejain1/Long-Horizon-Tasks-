"""MCP server: start_task, recall_context, record_outcome (PROJECT.md §11), with rollback rules (§8).

Tool descriptions are deliberately directive: the host decides when to call
us, so the descriptions say exactly when each tool must be called.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import InputRequiredResult
from pydantic import BaseModel, Field, create_model

from horizon.accounts import Accounts, OutOfCredits
from horizon.approvals import (
    Answer,
    Approval,
    answer_from_retry,
    ask_inline,
    can_ask,
    input_required,
    modern,
    resumed_state,
)
from horizon.config import Settings
from horizon.db import connect
from horizon.decision.layer import Option
from horizon.decision.scorers import make_scorer
from horizon.memrouter.embedding import make_embedder
from horizon.memrouter.router import MemRouter
from horizon.memrouter.store import EpisodeStore
from horizon.models import Condition
from horizon.service import Platform, ToolInputError
from horizon.taskstate.store import TaskStore

INSTRUCTIONS = """\
Horizon keeps task state and learns from outcomes across sessions. Use it on every coding task:
1. At the start of a task, call start_task with the user's request verbatim as the goal, then run the full
   test suite once before any edit (the baseline).
2. Before each significant decision or edit (choosing an approach, library, fix), call recall_context
   and use the returned task state and past outcomes. For a crucial choice (framework, database,
   architecture, key library), also call evaluate_options with 2-4 options before committing; on a close
   call, run its consequence plan and call submit_consequences.
3. After implementing and running tests, call record_outcome with the real test counts.
4. If record_outcome returns a rollback, restore the checkpoint it names and call recall_context before
   retrying. If it returns an escalation, stop and ask the user.
5. When the user asks what Horizon knows or why something was chosen: show_memories, explain_decision.
Never skip record_outcome after a test run: it is how the memory learns what works."""

START_TASK = """\
Create the task state for a new coding task. Call this FIRST, once per task, before any edits:
whenever the user asks for a feature, fix, refactor or other multi-step change.
Pass the user's request verbatim as `goal` (do not paraphrase), plus any constraints and your initial plan, and
the tests this task must make pass as `target_tests`. Returns a task_id; keep it and pass it to recall_context
and record_outcome. Then run the project's full test suite once, before any edit: tests that already fail
are recorded as the baseline and are not counted against your changes."""

RECALL_CONTEXT = """\
Get the task state plus past outcomes of similar decisions from memory. Call this BEFORE each
significant decision or edit (choosing an approach, library, architecture or fix), and again after a
failed attempt before retrying. Describe the decision point in `situation`. Past memories show what was
chosen, whether tests passed, and whether it went better or worse than predicted: prefer approaches that
worked and avoid ones that failed under similar conditions, and never repeat an approach listed in
task_state.retry.previous_failures. Keep the returned recall_id for record_outcome. If the task was escalated,
ask the user and pass their answer as `human_guidance`."""

EVALUATE_OPTIONS = """\
Score your options before committing to a crucial decision. Call this BEFORE choosing a framework, database,
architecture, key library, data model or anything else that is hard to reverse, affects many later steps, or
has real cost. Routine steps don't need it. Pass the decision point as `situation` and 2-4 realistic options,
with your cost/token/latency estimates where you have them. Returns a `decision`:
- "routine" or "clear_winner": implement `chosen`.
- "check_consequences": a close call. Run the `consequence_plan` (static checks, then one small spike per
  option, outside the working tree) and call submit_consequences with the results.
- "try_and_rollback": a close call where every option is cheap to undo: implement `chosen`; if tests
  regress, the rollback restores the checkpoint and you try the next option in `try_order`.
- "close_call": still nearly tied after the checks; `chosen` is the cheaper or more reversible one.
- "ask_human": high stakes (money, messages, data, or past severe failures): ask the user before acting.
- "unscored": no scorer is available; decide yourself on engineering merit.
Then implement, run the tests and call record_outcome with `chosen` as the option you implemented."""

SUBMIT_CONSEQUENCES = """\
Send the results of a consequence plan from evaluate_options (decision "check_consequences"). Call this after
running the plan's static checks and spikes, once per close call, with the `decision_id`. Report structured
results per option: which static checks passed, whether the spike ran and passed, test counts and numeric
metrics. No prose beyond one short note. Returns the final decision, re-scored with your results as evidence:
implement `chosen` (or ask the user if the decision is "ask_human"), then record_outcome."""

EXPLAIN_DECISION = """\
Explain a decision Horizon helped make: the evidence it used (past outcomes, consequence checks), the scores
per option and pass, why it chose what it chose, and what happened after. Call this when the user asks why an
option was chosen, or before revisiting a decision."""

SHOW_MEMORIES = """\
Show what Horizon remembers for this team: lessons, strategies, fear warnings and recent episodes, each with
its strength and evidence. Pass `query` to see the memories most similar to a situation. Call this when the user
asks what Horizon knows or remembers, or to find a memory_id for delete_memory."""

DELETE_MEMORY = """\
Remove a memory that is wrong or harmful from future recalls. Call this when the user says a memory is wrong,
or when a recalled memory is clearly mistaken. The user is asked to confirm. Episodes are archived (kept for
audit), lessons are deleted; either way the removal is logged. Fear lessons can't be deleted: use clear_fear."""

CLEAR_FEAR = """\
Clear a fear lesson (a warning created by a severe failure). Only a human may do this: the user is asked to
confirm and give their name, which is recorded. Call this only when the user asks to clear a warning."""

RECORD_OUTCOME = """\
Record what happened after you implemented a decision and ran the tests. Call this AFTER EVERY test run
that follows a change, whether tests passed or failed. Give the decision `situation` (as used in
recall_context), the option you implemented as `chosen`, the real test counts (tests_passed, tests_failed) and the recall_id.
If hooks captured the test run, the captured counts are used instead of yours. Set predicted_success to
the probability you expected it to work, if you estimated one. Set task_complete=true when the whole task
is done. If tests failed, give `failure_reason` in one sentence. Tests that already failed at the start of the task are
reported in `test_judgement` and don't count; a rollback happens only for regressions. `rollback` then says how
to restore the last good checkpoint (action "rollback": restore, then recall_context and retry differently) or
that the retry limit is reached (action "escalate": restore, stop and ask the user)."""


log = logging.getLogger(__name__)


def build_memrouter(settings: Settings, attention=None) -> MemRouter:
    store = EpisodeStore(connect(settings.db_url), settings.embedding_dim)
    return MemRouter(store, make_embedder(settings.embedder, settings.embedding_dim), settings, attention=attention)


def build_platform(settings: Settings, cwd: str | None = None) -> Platform:
    """Task state and memrouter get separate connections so one failing can't block the other."""
    tasks = TaskStore(connect(settings.db_url))

    scorers: dict = {}

    def scorer():
        if "s" not in scorers:  # built on first use; a failure (e.g. no API key) is retried next time
            scorers["s"] = make_scorer(settings.scorer, settings.jev_model, settings.llm_scorer_model)
        return scorers["s"]

    def memrouter() -> MemRouter:
        return build_memrouter(settings, attention=scorer)  # the scorer is also the attention filter (§6 step 5)

    return Platform(settings, tasks, memrouter, cwd=cwd, scorer_factory=scorer)


class StaticCheckIn(BaseModel):
    name: str = Field(description="Which check, e.g. \"pip install --dry-run\".")
    passed: bool


class SpikeIn(BaseModel):
    ran: bool = Field(description="False if you didn't build it (e.g. its static checks failed).")
    passed: bool | None = Field(None, description="Did the proof of concept do what the decision needs?")
    tests_passed: int | None = Field(None, ge=0)
    tests_failed: int | None = Field(None, ge=0)
    metrics: dict[str, float] = Field(default_factory=dict, description="Numbers that separate the options, "
                                      "e.g. latency_ms, tokens, cost_usd, lines_of_code, peak_memory_mb.")
    duration_s: float | None = Field(None, ge=0, description="How long the spike took.")


class ConsequenceIn(BaseModel):
    option: str = Field(description="The option's label, as in the consequence plan.")
    static_checks: list[StaticCheckIn] = Field(default_factory=list)
    spike: SpikeIn | None = None
    notes: str | None = Field(None, description="At most one short sentence; numbers belong in metrics.")


class OptionIn(BaseModel):
    label: str = Field(description="The option in a few words, e.g. \"PostgreSQL\".")
    description: str = Field("", description="One or two sentences: what it means for this project.")
    est_cost_usd: float | None = Field(None, ge=0, description="Your estimate of its running or build cost.")
    est_tokens: float | None = Field(None, ge=0, description="Your estimate of the tokens to implement it.")
    est_latency_ms: float | None = Field(None, ge=0, description="Your estimate of its latency, if relevant.")


def client_agent(ctx: Context | None) -> str | None:
    """Provenance for episodes (MEMROUTER.md §4, §11): the host that recorded it, e.g. "claude-code/2.1.283".
    Client-supplied, so it traces memories; it is not an identity check."""
    try:
        info = ctx.session.client_params.client_info
    except AttributeError:
        return None
    return f"{info.name}/{info.version}"[:100] if info else None


def _call(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except ToolInputError as exc:
        raise ToolError(str(exc)) from exc


def create_server(target: "Platform | Gateway") -> MCPServer:
    gw = target if isinstance(target, Gateway) else Gateway(lambda team: target)
    server = MCPServer(name="horizon", instructions=INSTRUCTIONS)

    @server.tool(description=START_TASK)
    async def start_task(
        goal: Annotated[str, Field(description="The user's request, verbatim.")],
        constraints: Annotated[list[str] | None, Field(description="Hard requirements and limits.")] = None,
        plan: Annotated[list[str] | None, Field(description="Initial plan as short steps.")] = None,
        open_issues: Annotated[list[str] | None, Field(description="Known unknowns or blockers.")] = None,
        project_id: Annotated[str | None, Field(description="Stable project name, e.g. the repo name.")] = None,
        target_tests: Annotated[list[str] | None, Field(description="Test files or ids this task must make pass, "
                                                                    "e.g. tests/test_api.py.")] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "start_task", lambda p: p.start_task(goal, constraints, plan, open_issues, project_id,
                                                                      target_tests, cwd=gw.project(ctx)))

    @server.tool(description=RECALL_CONTEXT)
    async def recall_context(
        task_id: Annotated[str, Field(description="From start_task.")],
        situation: Annotated[str, Field(description="The decision point, in one or two sentences.")],
        conditions: Annotated[
            list[Condition] | None,
            Field(description='Context that affects the outcome, e.g. {"key": "framework", "op": "=", '
                              '"value": "django"}.'),
        ] = None,
        token_budget: Annotated[int | None, Field(ge=100, le=20000,
                                                  description="Max tokens of memories to return.")] = None,
        human_guidance: Annotated[str | None, Field(description="The user's answer after an escalation; "
                                                                "resumes the task.")] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "recall_context",
                            lambda p: p.recall_context(task_id, situation, conditions, token_budget, human_guidance),
                            signals=lambda r: {"fear_warnings": r.get("fear_warnings", 0)})

    @server.tool(description=EVALUATE_OPTIONS)
    async def evaluate_options(
        task_id: Annotated[str, Field(description="From start_task.")],
        situation: Annotated[str, Field(description="The decision point, in one or two sentences.")],
        options: Annotated[list[OptionIn], Field(description="2-4 candidate options.")],
        conditions: Annotated[list[Condition] | None, Field(description="Same shape as recall_context.")] = None,
        crucial: Annotated[bool | None, Field(description="Set true/false only if you are sure; by default "
                                                          "Horizon decides whether this is crucial.")] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "evaluate_options",
                            lambda p: p.evaluate_options(task_id, situation, [Option(**o.model_dump()) for o in options],
                                                         conditions, crucial),
                            signals=_decision_signals, approval=HUMAN_CHOICE)

    @server.tool(description=SUBMIT_CONSEQUENCES)
    async def submit_consequences(
        task_id: Annotated[str, Field(description="From start_task.")],
        decision_id: Annotated[str, Field(description="From evaluate_options.")],
        results: Annotated[list[ConsequenceIn], Field(description="One entry per option you checked.")],
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "submit_consequences",
                            lambda p: p.submit_consequences(task_id, decision_id, [r.model_dump() for r in results]),
                            signals=_decision_signals, approval=HUMAN_CHOICE)

    @server.tool(description=RECORD_OUTCOME)
    async def record_outcome(
        task_id: Annotated[str, Field(description="From start_task.")],
        situation: Annotated[str, Field(description="The decision point, as given to recall_context.")],
        chosen: Annotated[str, Field(description="The option you implemented, in a short phrase.")],
        tests_passed: Annotated[int | None, Field(ge=0, description="Passing tests in the latest run.")] = None,
        tests_failed: Annotated[int | None, Field(ge=0, description="Failing or erroring tests.")] = None,
        success: Annotated[float | None, Field(ge=0, le=1,
                                               description="Only if there are no tests: 0..1.")] = None,
        alternatives: Annotated[list[str] | None, Field(description="Options you considered but rejected.")] = None,
        conditions: Annotated[list[Condition] | None, Field(description="Same shape as recall_context.")] = None,
        reason: Annotated[str | None, Field(description="Why you chose this option.")] = None,
        recall_id: Annotated[str | None, Field(description="From the recall_context that fed this decision.")] = None,
        predicted_success: Annotated[float | None, Field(ge=0, le=1,
                                                         description="Your prior probability it would work.")] = None,
        predicted_tokens: Annotated[float | None, Field(ge=0)] = None,
        predicted_cost_usd: Annotated[float | None, Field(ge=0)] = None,
        predicted_latency_ms: Annotated[float | None, Field(ge=0)] = None,
        tokens: Annotated[float | None, Field(ge=0, description="Tokens actually used, if known.")] = None,
        cost_usd: Annotated[float | None, Field(ge=0)] = None,
        latency_ms: Annotated[float | None, Field(ge=0)] = None,
        signal_type: Annotated[Literal["auto", "implicit", "human"],
                               Field(description="auto = tests/builds; implicit = user kept/reverted; "
                                                 "human = explicit approval.")] = "auto",
        severity: Annotated[Literal["normal", "severe"],
                            Field(description="severe = data loss, security issue, destructive action "
                                              "or large unplanned spend.")] = "normal",
        progress_note: Annotated[str | None, Field(description="One line for the task's progress log.")] = None,
        open_issues: Annotated[list[str] | None, Field(description="Replaces the open issues list.")] = None,
        task_complete: Annotated[bool, Field(description="True when the whole task is done.")] = False,
        failure_reason: Annotated[str | None, Field(description="If tests failed: why, in one sentence.")] = None,
        plan: Annotated[list[str] | None, Field(description="Replaces the plan, if it changed.")] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "record_outcome", lambda p: p.record_outcome(
            task_id, situation, chosen,
            success=success, tests_passed=tests_passed, tests_failed=tests_failed,
            alternatives=alternatives, conditions=conditions, reason=reason,
            predicted_success=predicted_success, predicted_tokens=predicted_tokens,
            predicted_cost_usd=predicted_cost_usd, predicted_latency_ms=predicted_latency_ms,
            tokens=tokens, cost_usd=cost_usd, latency_ms=latency_ms, signal_type=signal_type,
            severity=severity, recall_id=recall_id, progress_note=progress_note,
            open_issues=open_issues, task_complete=task_complete, failure_reason=failure_reason,
            plan=plan, agent_id=client_agent(ctx),
        ), signals=lambda r: {"rollback": (r.get("rollback") or {}).get("action")}, approval=ESCALATION)

    # --- inspection tools (PROJECT.md §10-11) --------------------------------------------

    @server.tool(description=EXPLAIN_DECISION)
    async def explain_decision(
        task_id: Annotated[str, Field(description="From start_task.")],
        decision_id: Annotated[str | None, Field(description="From evaluate_options; default: the task's latest "
                                                             "decision.")] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "explain_decision", lambda p: p.explain_decision(task_id, decision_id))

    @server.tool(description=SHOW_MEMORIES)
    async def show_memories(
        query: Annotated[str | None, Field(description="Show the memories most similar to this; default: lessons "
                                                       "and recent episodes.")] = None,
        kinds: Annotated[list[Literal["episode", "lesson", "strategy", "fear"]] | None,
                         Field(description="Only these kinds.")] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "show_memories", lambda p: p.show_memories(query, kinds, limit))

    @server.tool(description=DELETE_MEMORY)
    async def delete_memory(
        memory_id: Annotated[str, Field(description="From show_memories or recall_context.")],
        reason: Annotated[str, Field(description="Why it is wrong or harmful, in one sentence.")],
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "delete_memory", None, args={"memory_id": memory_id, "reason": reason},
                            approval=DELETE_CONFIRM)

    @server.tool(description=CLEAR_FEAR)
    async def clear_fear(
        lesson_id: Annotated[str, Field(description="The fear lesson's id, from show_memories.")],
        ctx: Context | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        return await gw.run(ctx, "clear_fear", None, args={"lesson_id": lesson_id}, approval=CLEAR_FEAR_CONFIRM)

    return server


# --- human approvals via MCP user input requests (PROJECT.md §10; see horizon/approvals.py) ------

class ConfirmIn(BaseModel):
    confirm: bool = Field(description="Yes, go ahead.")


class ClearFearIn(BaseModel):
    confirm: bool = Field(description="Yes, clear this warning.")
    your_name: str = Field(description="Recorded on the lesson as who cleared it.")


class GuidanceIn(BaseModel):
    guidance: str = Field(description="What should the agent do next?")


def _choice_schema(result: dict) -> type[BaseModel]:
    labels = [o["label"] for o in result["options"] if o["label"] not in set(result.get("eliminated") or [])]
    return create_model("HumanChoice", option=(Literal[tuple(labels)], Field(description="The option to take.")),
                        your_name=(str, Field("", description="Optional: recorded as the approver.")))


def _choice_question(result):
    """A high-stakes close call (decision "ask_human"): the user picks the option in the client."""
    if result.get("decision") != "ask_human":
        return None
    scores = "; ".join(f"{o['label']}: {o['composite']}" for o in result["options"])
    return (f"Horizon: a high-stakes choice is too close to call ({result['reason']}) Scores: {scores}. "
            f"Which option?", _choice_schema(result))


def _choice_apply(p: Platform, result: dict, answer: Answer) -> dict:
    if answer.action == "unsupported":
        return result  # the host asks in chat, as before
    if not answer.accepted:
        return {**result, "next": "The user didn't choose. Ask them in chat how to proceed before acting."}
    final = p.human_choice(result["task_id"], result["decision_id"], answer.data.option, answer.data.your_name or None)
    return {**result, "decision": "human_choice", "chosen": final["chosen"], "reason": final["reason"],
            "predicted_success": final["predicted_success"],
            "next": f"The user chose {final['chosen']}: implement it, then record_outcome."}


def _escalation_question(result):
    """The retry limit was hit (Phase 3 escalation): ask the user for guidance and resume."""
    rb = result.get("rollback") or {}
    if rb.get("action") != "escalate":
        return None
    failures = "; ".join(f"{f['chosen']}: {f['reason']}" for f in rb.get("failures", []))
    return f"Horizon: {rb['attempt']} attempts failed ({failures}). How should the agent proceed?", GuidanceIn


def _escalation_apply(p: Platform, result: dict, answer: Answer) -> dict:
    if not answer.accepted or not answer.data.guidance.strip():
        return result
    state = p.resume_with_guidance(result["task_id"], answer.data.guidance)
    return {**result, "task_status": state["status"], "human_guidance": answer.data.guidance,
            "rollback": {**result["rollback"], "next": "The user answered (see human_guidance) and the task is "
                         "resumed: restore the checkpoint, call recall_context, and follow their guidance."}}


def _delete_apply(p: Platform, args: dict, answer: Answer) -> dict:
    if answer.action != "unsupported" and not (answer.accepted and answer.data.confirm):
        return {"memory_id": args["memory_id"], "removed": False, "reason": "The user declined."}
    by = "user" if answer.accepted else "host (client can't ask the user)"
    return {**p.delete_memory(args["memory_id"], args["reason"], by), "removed": True}


def _clear_fear_apply(p: Platform, args: dict, answer: Answer) -> dict:
    if not answer.accepted or not answer.data.confirm or not answer.data.your_name.strip():
        return {"lesson_id": args["lesson_id"], "cleared": False, "reason": "Not confirmed by the user."}
    return {**p.clear_fear(args["lesson_id"], answer.data.your_name.strip()), "cleared": True}


HUMAN_CHOICE = Approval("decision", _choice_question, _choice_apply)
ESCALATION = Approval("escalation", _escalation_question, _escalation_apply)
DELETE_CONFIRM = Approval(
    "delete_memory", lambda args: (f"Horizon: remove memory {args['memory_id']} from future recalls? Reason given: "
                                   f"{args['reason']}", ConfirmIn), _delete_apply, before=True)
CLEAR_FEAR_CONFIRM = Approval(
    "clear_fear", lambda args: (f"Horizon: clear the fear lesson {args['lesson_id']}? It warns about a past severe "
                                "failure. Only clear it if you're sure the danger is gone.", ClearFearIn),
    _clear_fear_apply, before=True,
    required="clear_fear needs a human's confirmation and this client can't ask the user. The user can run "
             "`horizon clear-fear {lesson_id} --by <name>` in a terminal instead.")


def _decision_signals(r: dict) -> dict:
    return {"decision": r.get("decision"), "settled_by": r.get("settled_by"),
            "eliminated": len(r.get("eliminated") or []), "spike_tokens_avoided": r.get("spike_tokens_avoided", 0)}


class Gateway:
    """Which platform serves a call, what it costs, and the human approvals it needs (PROJECT.md §3, §10).

    Local stdio: one platform, no key, no credits. Hosted HTTP: the team comes from the caller's API key in the
    MCP request's headers, each team gets its own Platform (same stores, team-scoped), and each call is
    checked against and charged to the team's credits, with the savings signals it produced.
    """

    def __init__(self, platform_for, accounts: Accounts | None = None, dev_key: str | None = None):
        self.platform_for, self.accounts, self.dev_key = platform_for, accounts, dev_key

    def project(self, ctx: Context | None) -> str | None:
        """Hosted mode: the client's hashed project key (X-Horizon-Project), matched against its hooks."""
        if self.accounts is None:
            return None
        try:
            value = ctx.headers.get("x-horizon-project") if ctx is not None and ctx.headers else None
        except Exception:
            return None
        return value[:64] if value and value.startswith("prj_") else None

    def team(self, ctx: Context | None) -> str | None:
        try:
            headers = ctx.headers if ctx is not None else None
        except Exception:
            headers = None
        if not headers or self.accounts is None:
            return None
        token = bearer_token(headers.get("authorization"), headers.get("x-api-key"))
        if self.dev_key and hmac.compare_digest(token.encode(), self.dev_key.encode()):
            return None  # the static dev key: local team, unmetered
        return self.accounts.verify_key(token)

    async def run(self, ctx, tool: str, fn, signals=None, approval: Approval | None = None,
                  args: dict | None = None) -> dict | InputRequiredResult:
        team = self.team(ctx)
        p = self.platform_for(team)

        # Round 2 of a 2026-07-28 approval: the signed state carries round 1's work; nothing is redone.
        state = resumed_state(ctx) if approval else None
        if state is not None:
            if state.get("tool") != tool or state.get("team") != team or (args is not None and state["args"] != args):
                raise ToolError("This answer belongs to a different request; call the tool again.")
            subject = state["args"] if approval.before else state["result"]
            question = approval.question(subject)
            answer = answer_from_retry(ctx, question[1]) if question else Answer("unsupported")
            return self._finish(ctx, p, team, approval, subject, answer, state.get("task_id"))

        if team is not None:
            try:
                self.accounts.check(team, tool)
            except OutOfCredits as exc:
                raise ToolError(str(exc)) from exc
        result = None if approval and approval.before else _call(fn, p)
        if team is not None and result is not None:
            self._charge(ctx, team, tool, result.get("task_id"), signals(result) if signals else {})
        if approval is None:
            return result

        subject = args if approval.before else result
        question = approval.question(subject)
        if question is None:
            return result
        message, schema = question
        task_id = (result or {}).get("task_id")
        if not can_ask(ctx):
            if approval.required:
                raise ToolError(approval.required.format(**(args or {})))
            return self._finish(ctx, p, team, approval, subject, Answer("unsupported"), task_id)
        if modern(ctx):
            return input_required(message, schema, {"tool": tool, "team": team, "args": args, "result": result,
                                                    "task_id": task_id})
        return self._finish(ctx, p, team, approval, subject, await ask_inline(ctx, message, schema), task_id)

    def _finish(self, ctx, p: Platform, team, approval: Approval, subject, answer: Answer, task_id) -> dict:
        question = approval.question(subject)
        data = answer.data.model_dump() if answer.data is not None else {}
        p.log_approval(task_id, approval.kind, answer.action, question[0] if question else approval.kind,
                       answer=data.get("option") or data.get("guidance") or
                       (str(data["confirm"]) if "confirm" in data else None),
                       approver=data.get("your_name") or None)
        final = _call(lambda platform: approval.apply(platform, subject, answer), p)
        if team is not None and answer.accepted:
            self._charge(ctx, team, "human_approval", task_id, {"human_approvals": 1})
        return final

    def _charge(self, ctx, team: str, tool: str, task_id: str | None, signals: dict) -> None:
        session = None
        try:
            session = ctx.headers.get("mcp-session-id")
        except Exception:
            pass
        self.accounts.charge(team, tool, session_id=session, task_id=task_id, signals=signals)


def bearer_token(authorization: str | None, x_api_key: str | None = None) -> str:
    auth = authorization or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else (x_api_key or "").strip()




class APIKeyMiddleware:
    """API key check for the MCP endpoint of the HTTP transport: the static dev key (local team) or a team key
    (PROJECT.md §3). Other paths (the account pages) pass through: they have their own sign-in."""

    def __init__(self, app, api_key: str | None = None, accounts: Accounts | None = None, prefix: str = "/"):
        self.app, self.accounts, self.prefix = app, accounts, prefix
        self.api_key = api_key.encode() if api_key else None

    def _valid(self, token: str) -> bool:
        if self.api_key and hmac.compare_digest(token.encode(), self.api_key):
            return True
        return bool(self.accounts and self.accounts.verify_key(token))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith(self.prefix):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
            if not self._valid(bearer_token(headers.get("authorization"), headers.get("x-api-key"))):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"invalid or missing API key"}'})
                return
        await self.app(scope, receive, send)


def http_app(settings: Settings, cwd: str | None = None):
    """The hosted server: the account page and JSON API at `/`, MCP at `/mcp` (API key, metered credits)."""
    import dataclasses

    from horizon.web import create_web_app

    base = build_platform(settings, cwd)
    accounts = Accounts(base.tasks.db)
    platforms: dict[str, Platform] = {}

    def platform_for(team: str | None) -> Platform:
        if team is None:
            return base  # local stdio or the static dev key
        if team not in platforms:
            platforms[team] = Platform(dataclasses.replace(settings, team_id=team), base.tasks,
                                       base._memrouter_factory, cwd=base.cwd, scorer_factory=base._scorer_factory)
        return platforms[team]

    mcp_app = create_server(Gateway(platform_for, accounts, settings.dev_api_key)).streamable_http_app()
    web = create_web_app(accounts, base.tasks, lifespan=lambda app: mcp_app.router.lifespan_context(mcp_app))
    web.mount("/", APIKeyMiddleware(mcp_app, settings.dev_api_key, accounts, prefix="/mcp"))
    return web


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    settings = Settings.from_env()
    cwd = os.environ.get("HORIZON_PROJECT_DIR")
    if transport == "stdio":
        create_server(build_platform(settings, cwd)).run("stdio")
    else:
        import uvicorn

        uvicorn.run(http_app(settings, cwd), host=host, port=port)
