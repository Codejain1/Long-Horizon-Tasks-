"""Replay a session's events into states and transitions (v2 Phase 0).

The state has a general part, kept here for every domain, and the domain's part, kept by its adapter. Each
`step` event is one transition: the state before, the action and its result, and the state after. That's the
trainable format the world model learns from (PROJECT.md §6: "log data in a trainable format from day one").
Replay is pure and deterministic: the same events always give the same states.
"""

from __future__ import annotations

from dataclasses import dataclass

from horizon.adapters.base import DomainAdapter
from horizon.eventlog.schema import Action, Event, Result


def initial_core() -> dict:
    return {"steps": 0, "prompts": 0, "turns": 0, "compactions": 0, "risky": 0, "tokens": 0.0}


def reduce_core(core: dict, event: Event) -> dict:
    core = dict(core)
    if event.kind == "step":
        core["steps"] += 1
        core["risky"] += int(bool(event.action and event.action.risky))
    elif event.kind == "prompt":
        core["prompts"] += 1
    elif event.kind == "turn_end":
        core["turns"] += 1
        core["tokens"] += event.usage.get("tokens", 0.0)
    elif event.kind == "compaction":
        core["compactions"] += 1
    return core


@dataclass(frozen=True)
class Transition:
    event_id: str
    before: dict
    action: Action
    result: Result
    after: dict


def replay(events: list[Event], adapter: DomainAdapter) -> tuple[list[dict], list[Transition]]:
    """The state after every event (states[i] follows events[i]) and one transition per step."""
    state = {"core": initial_core(), "domain": adapter.initial_state()}
    states, transitions = [], []
    for event in events:
        after = {"core": reduce_core(state["core"], event), "domain": adapter.reduce(state["domain"], event)}
        if event.kind == "step" and event.action is not None:
            transitions.append(Transition(event.id, state, event.action, event.result or Result(), after))
        states.append(after)
        state = after
    return states, transitions
