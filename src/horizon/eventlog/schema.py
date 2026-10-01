"""The event log's schema (v2 Phase 0): what the hooks observed, in a form every domain shares.

Events are observations, not interpretations. State and transitions are derived from them by a domain adapter
(`horizon.adapters`), so a better state representation can be re-derived later from the same history.

Nothing here is coding-specific: a domain names its own actions (`Action.name`) and puts its measurements in
`Result.metrics` / `Result.facts`. Every text field is redacted before it's built (PROJECT.md §12).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from horizon.models import new_id, now

EVENT_SCHEMA_VERSION = 1

# session_start / session_end: the session's boundaries. prompt: a user message. step: an action the agent took,
# with its result. turn_end: the agent finished a turn (carries the turn's real token usage). compaction: the
# host is about to compact the context.
EventKind = Literal["session_start", "prompt", "step", "turn_end", "compaction", "session_end"]
# Steps that record what Horizon told the agent rather than what the agent did: not transitions.
HORIZON_ACTIONS = frozenset({"dead_end_warning"})


class Action(BaseModel):
    name: str  # the domain's vocabulary, e.g. "edit", "bash", "test_run"
    target: str | None = None  # a short, redacted identifier: a project-relative path, a command summary
    risky: bool = False
    risk: str | None = None  # why it's risky, e.g. "migration", "destructive"


class Result(BaseModel):
    ok: bool | None = None  # None when the host doesn't say
    metrics: dict[str, float] = Field(default_factory=dict)  # counts and measurements
    facts: dict[str, Any] = Field(default_factory=dict)  # small, redacted values, e.g. failing test ids


class Event(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    schema_version: int = EVENT_SCHEMA_VERSION
    team_id: str
    project: str  # the local project directory, or its hashed key in hosted mode
    session_id: str | None = None
    domain: str
    source: str  # the host, e.g. "claude-code"
    kind: EventKind
    text: str | None = None  # redacted: a prompt, a compaction trigger, an end reason
    action: Action | None = None
    result: Result | None = None
    usage: dict[str, float] = Field(default_factory=dict)  # tokens, input, output, cache_*, turns, latency_ms
    data: dict[str, Any] = Field(default_factory=dict)  # small extras, e.g. how a session started
    at: datetime = Field(default_factory=now)
