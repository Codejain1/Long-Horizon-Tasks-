"""MCP server: start_task, recall_context, record_outcome (PROJECT.md §11), with rollback rules (§8).

Tool descriptions are deliberately directive: the host decides when to call
us, so the descriptions say exactly when each tool must be called.
"""

from __future__ import annotations

import hmac
import os
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

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
   architecture, key library), also call evaluate_options with 2-4 options before committing.
3. After implementing and running tests, call record_outcome with the real test counts.
4. If record_outcome returns a rollback, restore the checkpoint it names and call recall_context before
   retrying. If it returns an escalation, stop and ask the user.
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
- "close_call": the options are nearly tied; `chosen` is the cheaper or more reversible one.
- "ask_human": high stakes (money, messages, data, or past severe failures): ask the user before acting.
- "unscored": no scorer is available; decide yourself on engineering merit.
Then implement, run the tests and call record_outcome with `chosen` as the option you implemented."""

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


def build_platform(settings: Settings, cwd: str | None = None) -> Platform:
    """Task state and memrouter get separate connections so one failing can't block the other."""
    tasks = TaskStore(connect(settings.db_url))

    def memrouter() -> MemRouter:
        store = EpisodeStore(connect(settings.db_url), settings.embedding_dim)
        return MemRouter(store, make_embedder(settings.embedder, settings.embedding_dim), settings)

    scorers: dict = {}

    def scorer():
        if "s" not in scorers:  # built on first use; a failure (e.g. no API key) is retried next time
            scorers["s"] = make_scorer(settings.scorer, settings.jev_model, settings.llm_scorer_model)
        return scorers["s"]

    return Platform(settings, tasks, memrouter, cwd=cwd, scorer_factory=scorer)


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


def create_server(platform: Platform) -> MCPServer:
    server = MCPServer(name="horizon", instructions=INSTRUCTIONS)

    @server.tool(description=START_TASK)
    def start_task(
        goal: Annotated[str, Field(description="The user's request, verbatim.")],
        constraints: Annotated[list[str] | None, Field(description="Hard requirements and limits.")] = None,
        plan: Annotated[list[str] | None, Field(description="Initial plan as short steps.")] = None,
        open_issues: Annotated[list[str] | None, Field(description="Known unknowns or blockers.")] = None,
        project_id: Annotated[str | None, Field(description="Stable project name, e.g. the repo name.")] = None,
        target_tests: Annotated[list[str] | None, Field(description="Test files or ids this task must make pass, "
                                                                    "e.g. tests/test_api.py.")] = None,
    ) -> dict[str, Any]:
        return _call(platform.start_task, goal, constraints, plan, open_issues, project_id, target_tests)

    @server.tool(description=RECALL_CONTEXT)
    def recall_context(
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
    ) -> dict[str, Any]:
        return _call(platform.recall_context, task_id, situation, conditions, token_budget, human_guidance)

    @server.tool(description=EVALUATE_OPTIONS)
    def evaluate_options(
        task_id: Annotated[str, Field(description="From start_task.")],
        situation: Annotated[str, Field(description="The decision point, in one or two sentences.")],
        options: Annotated[list[OptionIn], Field(description="2-4 candidate options.")],
        conditions: Annotated[list[Condition] | None, Field(description="Same shape as recall_context.")] = None,
        crucial: Annotated[bool | None, Field(description="Set true/false only if you are sure; by default "
                                                          "Horizon decides whether this is crucial.")] = None,
    ) -> dict[str, Any]:
        return _call(platform.evaluate_options, task_id, situation,
                     [Option(**o.model_dump()) for o in options], conditions, crucial)

    @server.tool(description=RECORD_OUTCOME)
    def record_outcome(
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
    ) -> dict[str, Any]:
        return _call(
            platform.record_outcome, task_id, situation, chosen,
            success=success, tests_passed=tests_passed, tests_failed=tests_failed,
            alternatives=alternatives, conditions=conditions, reason=reason,
            predicted_success=predicted_success, predicted_tokens=predicted_tokens,
            predicted_cost_usd=predicted_cost_usd, predicted_latency_ms=predicted_latency_ms,
            tokens=tokens, cost_usd=cost_usd, latency_ms=latency_ms, signal_type=signal_type,
            severity=severity, recall_id=recall_id, progress_note=progress_note,
            open_issues=open_issues, task_complete=task_complete, failure_reason=failure_reason,
            plan=plan, agent_id=client_agent(ctx),
        )

    return server


class APIKeyMiddleware:
    """Bearer-token check for the HTTP transport (static dev key until Phase 7)."""

    def __init__(self, app, api_key: str):
        self.app = app
        self.api_key = api_key.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"")
            token = auth[7:] if auth.lower().startswith(b"bearer ") else headers.get(b"x-api-key", b"")
            if not hmac.compare_digest(token, self.api_key):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"invalid or missing API key"}'})
                return
        await self.app(scope, receive, send)


def http_app(settings: Settings, cwd: str | None = None):
    if not settings.dev_api_key:
        raise SystemExit("HORIZON_DEV_API_KEY must be set for the HTTP transport.")
    server = create_server(build_platform(settings, cwd))
    return APIKeyMiddleware(server.streamable_http_app(), settings.dev_api_key)


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    settings = Settings.from_env()
    cwd = os.environ.get("HORIZON_PROJECT_DIR")
    if transport == "stdio":
        create_server(build_platform(settings, cwd)).run("stdio")
    else:
        import uvicorn

        uvicorn.run(http_app(settings, cwd), host=host, port=port)
