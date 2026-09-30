"""Jev as outcome judge (v2 Phase 1 track B): an outcome for session segments with no test run.

Without tests the log still holds evidence: what the user asked, what they said next (a correction, "still
broken", or thanks), and what the agent did (edits, commands, turns). Jev reads that, never code or output, and
places the segment on ordered levels; a correction classifier question per follow-up says which messages pushed
back. The result is `implicit`: memory weighs it below a test run (MEMROUTER.md §5). A segment with nothing to go
on (no edit and no follow-up) gets no outcome, as before; nor does an unsure verdict, or a "done" contradicted by
a last message that pushes back.
"""

from __future__ import annotations

from horizon.adapters.base import Outcome
from horizon.decision.scorers import noul, score
from horizon.eventlog.schema import Event

LEVELS = ["not done: the user says it failed, or gave up", "not done yet: the user is still asking for fixes",
          "partly done", "done, not confirmed by the user", "done: the user confirmed it works"]
MAX_FOLLOW_UPS = 8
MAX_TEXT = 300
MIN_CONFIDENCE = 0.5


def evidence(events: list[Event]) -> dict | None:
    """What the judge reads: prompts (already redacted) and activity counts. None when there's nothing to judge."""
    prompts = [e.text[:MAX_TEXT] for e in events if e.kind == "prompt" and e.text]
    steps = [e for e in events if e.kind == "step" and e.action is not None]
    edits = [e for e in steps if e.action.name in ("edit", "write")]
    if not prompts or not (edits or len(prompts) > 1):
        return None
    end = next((e.text for e in reversed(events) if e.kind == "session_end"), None)
    return {"task": prompts[0], "follow_ups": prompts[1:][-MAX_FOLLOW_UPS:],
            "activity": {"edits": len(edits), "files_changed": len({e.action.target for e in edits}),
                         "commands": sum(e.action.name == "bash" for e in steps),
                         "turns": sum(e.kind == "turn_end" for e in events)},
            "ended": end or "went idle"}


def questions(follow_ups: int) -> dict[str, dict]:
    qs = {"outcome": score("How far did the agent get with `task`, judging by what the user said next "
                           "(`follow_ups`, in order) and what the agent did (`activity`)?", LEVELS)}
    for i in range(follow_ups):
        qs[f"correction_{i}"] = noul(f"Does `follow_ups[{i}]` tell the agent its earlier work was wrong, broken or "
                                     "not what was asked?")
    return qs


def judge_outcome(scorer, events: list[Event]) -> tuple[Outcome | None, str | None]:
    """The segment's outcome as Jev judges it, or why there's none."""
    state = evidence(events)
    if state is None:
        return None, "no ground truth"
    answers = scorer.ask(state, questions(len(state["follow_ups"])))
    verdict = answers["outcome"]
    if verdict.confidence is not None and verdict.confidence < MIN_CONFIDENCE:
        return None, "judge unsure"
    pushed_back = [answers[f"correction_{i}"].value > 0.5 for i in range(len(state["follow_ups"]))]
    if pushed_back and pushed_back[-1] and verdict.value > 0.5:  # "done", yet the user's last word is a correction
        return None, "judge unsure"
    corrections = sum(pushed_back)
    return Outcome(success=round(verdict.value, 3), signal="implicit",
                   metrics={"corrections": float(corrections), **{k: float(v) for k, v in state["activity"].items()}},
                   facts={"judged_by": scorer.name, "confidence": verdict.confidence}), None
