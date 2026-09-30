"""What similar past sessions taught (v2 Phase 1, `session_memory`): context for a lean task's first prompt.

The point is less trial and error: an agent that knows which tests tripped up a similar change, where it landed
and how the last attempt ended can check those first instead of trying one thing, then another. So only past
sessions that hit trouble (a failing test run on the way, or failures left at the end) are shown; a clean
success's file list only adds reading. Everything comes
from observations: memory recalls the similar session episodes (and its lessons and fears), and each episode's
event-log segment says what happened in it. Paths are project-relative, test ids are ids; never code or output.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from horizon.eventlog.episodes import CHOSEN, TASK_TYPE, SessionEpisodes
from horizon.eventlog.schema import Event
from horizon.models import Condition

MAX_SESSIONS = 3
MAX_CHARS = 1200  # about 300 tokens, whatever memory holds
MAX_FILES = 6
MAX_TESTS = 4
MAX_TASK = 140
TOKEN_BUDGET = 600  # recall's own budget for the memories it returns
HEADER = "Horizon: similar past sessions in this project (what happened then; check it still holds):"


@dataclass
class Trace:
    """One session segment, as the event log saw it."""

    files: list[str] = field(default_factory=list)  # changed, in the order first touched
    runs: int = 0
    failed_runs: int = 0
    tripped: list[str] = field(default_factory=list)  # failed on the way, passing by the end
    still_failing: list[str] = field(default_factory=list)
    last: dict | None = None  # the last test run's counts


def trace(events: list[Event]) -> Trace:
    t, seen = Trace(), []
    for e in events:
        if e.kind != "step" or e.action is None:
            continue
        if e.action.name in ("edit", "write") and e.action.target and e.action.target not in t.files:
            t.files.append(e.action.target)
        elif e.action.name == "test_run":
            m, failing = (e.result.metrics if e.result else {}), (e.result.facts.get("failing") if e.result else None)
            t.runs += 1
            t.failed_runs += int(m.get("failed", 0) > 0)
            t.last = {"passed": int(m.get("passed", 0)), "total": int(m.get("total", 0))}
            t.still_failing = list(failing or [])
            seen += [f for f in failing or [] if f not in seen]
    t.tripped = [f for f in seen if f not in t.still_failing]
    return t


def distinct(task: str, goal: str, shared: int = 40) -> str:
    """A past task without the opening it shares with the new goal (a standing preamble), so its label says
    what that task was about."""
    n = len(os.path.commonprefix([task, goal]))
    n = task.rfind(" ", 0, n + 1) if n < len(task) else n  # back to a word boundary
    return "…" + task[n:].lstrip() if n >= shared and task[n:].strip() else task


def _clip(items: list[str], most: int) -> str:
    return ", ".join(items[:most]) + (f" (+{len(items) - most} more)" if len(items) > most else "")


def describe(task: str, t: Trace) -> str:
    task = task if len(task) <= MAX_TASK else task[: MAX_TASK - 1] + "…"
    parts = []
    if t.last:
        ended = f"{t.last['passed']}/{t.last['total']} tests passing"
        parts.append(f"ended with {ended}" + (" on the first test run" if t.runs == 1 and not t.failed_runs else
                                              f" after {t.runs} test runs ({t.failed_runs} failing)"))
    if t.files:
        parts.append("changed " + _clip(t.files, MAX_FILES))
    if t.tripped:
        parts.append("failed along the way: " + _clip(t.tripped, MAX_TESTS))
    if t.still_failing:
        parts.append("still failing at the end: " + _clip(t.still_failing, MAX_TESTS))
    return f'- "{task}": ' + "; ".join(parts) + "." if parts else f'- "{task}"'


def past_sessions(log: SessionEpisodes, team_id: str, project: str, text: str, memory: Callable[[], object],
                  session: str | None = None, min_similarity: float = 0.8) -> str | None:
    """The context for a new task, or None when memory has nothing clearly similar (`min_similarity`; fears keep
    recall's own bar). `memory` builds the memrouter, only if the project has session episodes at all (loading
    the embedder is the slow part)."""
    if not log.has_episodes(team_id, project):
        return None
    found = memory().recall(team_id=team_id, situation=text, conditions=[Condition(key="task_type", value=TASK_TYPE)],
                            token_budget=TOKEN_BUDGET, project_id=project)
    sessions: dict[str, int] = {}  # line -> how many sessions went exactly that way
    lessons, fears = [], []
    for m in found.memories:
        if m.kind == "fear":
            fears.append(f"- {m.outcome}")
        elif m.similarity < min_similarity:
            continue
        elif m.kind in ("lesson", "strategy"):
            lessons.append(f"- {m.outcome}")
        elif m.kind == "episode" and m.chosen == CHOSEN:
            seg = log.segment_of(team_id, m.episode_id)
            t = trace(log.segment_events(team_id, seg)) if seg and seg.session_id != session else None
            if t and (t.failed_runs or t.still_failing):  # a clean success has no trial and error to save
                line = describe(distinct(m.situation, text), t)
                if line in sessions or len(sessions) < MAX_SESSIONS:
                    sessions[line] = sessions.get(line, 0) + 1
    if not (sessions or lessons or fears):
        return None
    lines = [HEADER, *(line if n == 1 else f"{line} ({n} sessions went this way)" for line, n in sessions.items())]
    lines += (["Lessons:", *lessons] if lessons else []) + (["Warnings:", *fears] if fears else [])
    out = []
    for line in lines:  # whole lines only, within the cap
        if sum(len(x) + 1 for x in out) + len(line) > MAX_CHARS:
            break
        out.append(line)
    return "\n".join(out) if len(out) > 1 else None
