"""Data model shared by task state, memrouter and the MCP tools.

Episode fields follow MEMROUTER.md §4. Task state follows PROJECT.md §8.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EPISODE_SCHEMA_VERSION = 1

SignalType = Literal["auto", "implicit", "human"]
PredictionSource = Literal["jev", "sim", "memory", "host"]
Severity = Literal["normal", "severe"]
ConditionOp = Literal["=", "!=", ">", ">=", "<", "<=", "in", "contains"]


def now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Condition(BaseModel):
    """A condition an outcome depends on, e.g. {key: "rps", op: ">", value: 10000}."""

    key: str
    op: ConditionOp = "="
    value: Any


class OptionRef(BaseModel):
    label: str
    details: str | None = None


class Predicted(BaseModel):
    """What was expected before acting. ``success`` is a probability (0..1)."""

    success: float | None = Field(default=None, ge=0, le=1)
    tokens: float | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: PredictionSource = "host"


class TestResults(BaseModel):
    """Counts only; raw test output is never stored (PROJECT.md §12)."""

    __test__ = False  # not a pytest test class

    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    total: int = Field(ge=0)
    source: Literal["hook", "host"] = "host"
    runner: str | None = None

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


class Actual(BaseModel):
    """What happened. ``success`` is the test pass rate (0..1)."""

    success: float = Field(ge=0, le=1)
    tokens: float | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    test_results: TestResults | None = None
    signal_type: SignalType = "auto"


class Scope(BaseModel):
    team_id: str
    project_id: str | None = None
    task_id: str | None = None


class Provenance(BaseModel):
    agent_id: str | None = None
    task_id: str | None = None
    created_at: datetime = Field(default_factory=now)


class Episode(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ep"))
    scope: Scope
    situation: str
    conditions: list[Condition] = Field(default_factory=list)
    chosen: OptionRef
    alternatives: list[OptionRef] = Field(default_factory=list)
    predicted: Predicted
    actual: Actual
    surprise: float
    low_confidence: bool = False
    severity: Severity = "normal"
    provenance: Provenance = Field(default_factory=Provenance)
    recall_id: str | None = None  # the recall that fed this decision, for Phase 6 link learning
    archived_at: datetime | None = None
    embedding_model: str | None = None
    schema_version: int = EPISODE_SCHEMA_VERSION


def episode_text(situation: str, conditions: list[Condition]) -> str:
    """Text embedded for similarity: the decision point, not the choice, so the write
    side and the read side (where the choice isn't known yet) look alike."""
    parts = [situation.strip()]
    if conditions:
        parts.append("conditions: " + ", ".join(f"{c.key} {c.op} {c.value}" for c in conditions))
    return "\n".join(parts)


class MemoryItem(BaseModel):
    """One recalled memory, as returned to the host."""

    episode_id: str
    situation: str
    chosen: str
    outcome: str
    success: float
    surprise: float
    similarity: float
    conditions: list[Condition] = Field(default_factory=list)
    severity: Severity = "normal"
    recorded_at: datetime


class ProgressEntry(BaseModel):
    at: datetime = Field(default_factory=now)
    note: str


class DecisionEntry(BaseModel):
    at: datetime = Field(default_factory=now)
    situation: str
    chosen: str
    reason: str | None = None
    episode_id: str | None = None
    success: float | None = None


class TaskState(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    team_id: str
    project_id: str | None = None
    goal: str  # verbatim, never rewritten
    constraints: list[str] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    progress: list[ProgressEntry] = Field(default_factory=list)
    decisions: list[DecisionEntry] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    status: Literal["active", "completed"] = "active"
    cwd: str | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class TestCapture(BaseModel):
    """Real test counts captured by the PostToolUse hook."""

    __test__ = False

    id: str = Field(default_factory=lambda: new_id("cap"))
    session_id: str | None = None
    cwd: str | None = None
    command: str
    runner: str
    passed: int
    failed: int
    total: int
    created_at: datetime = Field(default_factory=now)
    consumed_by: str | None = None  # episode id that used it
