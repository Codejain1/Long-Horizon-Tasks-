"""The domain adapter interface (v2 Phase 0): how one domain's events become state, risk and outcomes.

Horizon's core (event log, memory, world model, context) knows nothing about coding. An adapter supplies the
domain's meaning:

- `reduce`: the domain's part of the state after an event. Pure: it returns a new dict.
- `classify`: whether an action is risky (hard to undo), and why. Foresight triggers on these (Phase 4).
- `outcome`: a session's ground truth, from its events. Memory learns from it (Phase 1).
- `situation`: the text a session is about, for similarity search and the world model.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from horizon.eventlog.schema import Event


class Outcome(BaseModel):
    success: float | None  # 0..1; None when the session produced no ground truth
    signal: str = "auto"
    metrics: dict[str, float] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)


class DomainAdapter(Protocol):
    domain: str

    def initial_state(self) -> dict: ...

    def reduce(self, state: dict, event: Event) -> dict: ...

    def classify(self, action: str, raw: dict) -> tuple[bool, str | None]: ...

    def outcome(self, events: list[Event]) -> Outcome | None: ...

    def situation(self, events: list[Event]) -> str | None: ...


def first_prompt(events: list[Event]) -> str | None:
    """The default situation: what the user asked first in the session."""
    return next((e.text for e in events if e.kind == "prompt" and e.text), None)
