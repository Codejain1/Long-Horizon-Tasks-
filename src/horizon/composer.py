"""The context composer (v2 Phase 1 track D): what lean shows the agent, and when, within a budget.

What Horizon injects is a few hundred tokens against the hundreds of thousands a session spends; what it costs
is what the agent does with it (the first past-sessions run read every file a note listed). So the composer
picks by value, not only by size:
- **Session start** carries what holds whatever the task: the task being resumed, team rules, every constraint
  earlier tasks set (not only the last three tasks'), checks that keep failing here, and what the last tasks
  were, labelled by what differs between them rather than the preamble they share. Each line once, whole lines
  only, and when the budget is tight the least essential kind goes first.
- **The first prompt** knows the goal, so it is where goal-dependent context goes: the past-sessions note, and
  only when the session forecast (track C) expects trouble on this task. Similar sessions that went cleanly
  have no trial and error to save.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

MAX_RULE = 500
MAX_GOAL_SHOWN = 400
MAX_LABEL = 120
MAX_CONSTRAINTS = 20
MAX_RULES = 20
RECENT_SHOWN = 3
RECENT_WINDOW = 10  # tasks whose goals reveal the project's shared preamble


@dataclass(frozen=True)
class Section:
    key: str
    heading: str
    keep: int  # lower is kept first when the budget is tight


@dataclass(frozen=True)
class Item:
    section: str
    text: str


SESSION_START = (  # display order
    Section("active", "Active task in this project (continue it; don't start over):", 0),
    Section("pitfalls", "Checks that failed in several earlier sessions here (get them right the first time):", 3),
    Section("team_rules", "Team rules for every project (apply them here too):", 1),
    Section("constraints", "Constraints set by earlier tasks here (they still apply unless the user changes them):",
            2),
    Section("earlier", "Recent tasks in this project:", 4),
)


# After a compaction the agent keeps only the host's summary, so the task comes back whole, with where it got to.
REANCHOR = (
    Section("active", "The task this session is working on (restated: the context was just compacted):", 0),
    Section("progress", "Where this session had got to:", 3),
    Section("constraints", SESSION_START[3].heading, 1),
    Section("team_rules", SESSION_START[2].heading, 2),
    Section("pitfalls", SESSION_START[1].heading, 4),
    Section("earlier", SESSION_START[4].heading, 5),
)
MAX_GOAL_REANCHOR = 1500
MAX_NOTES = 5
MAX_FILES = 10


def distinct(task: str, goal: str, shared: int = 40) -> str:
    """A task without the opening it shares with another (a standing preamble), so its label says what that task
    was about."""
    n = len(os.path.commonprefix([task, goal]))
    n = task.rfind(" ", 0, n + 1) if n < len(task) else n  # back to a word boundary
    return "…" + task[n:].lstrip() if n >= shared and task[n:].strip() else task


def clip(text: str, most: int) -> str:
    return text if len(text) <= most else text[: most - 1] + "…"


def label(goal: str, goals: list[str], most: int = MAX_LABEL) -> str:
    """A goal without the preamble all the project's recent goals open with, clipped. Only what they all share:
    two tickets also share "Ticket #1", which is not preamble."""
    shared = os.path.commonprefix(goals) if len(goals) > 1 else ""
    sentence = shared.rfind(". ") + 2  # a preamble is whole sentences: "Ticket #" is where the ticket starts
    return clip(distinct(goal, shared[:sentence] if sentence >= 40 else shared) if shared else goal, most)


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(".").casefold()


def compose(preamble: str, sections: tuple[Section, ...], items: list[Item], budget: int) -> tuple[str, dict]:
    """The preamble, then each section's items under its heading, within `budget` characters. Sections are filled
    in `keep` order; an item that doesn't fit is dropped whole, and so is everything in less essential sections
    (a shorter item of its own section may still fit). One already shown in another section isn't repeated.
    Returns the text and how many items each section dropped."""
    chosen: dict[str, list[str]] = {s.key: [] for s in sections}
    seen, dropped, used, floor = set(), {}, len(preamble), None
    for s in sorted(sections, key=lambda s: s.keep):
        for item in (i for i in items if i.section == s.key):
            key = _key(item.text)
            if key in seen:
                continue
            seen.add(key)
            cost = len(item.text) + 1 + (0 if chosen[s.key] else len(s.heading) + 1)
            if (floor is not None and s.keep > floor) or used + cost > budget:
                floor = s.keep if floor is None else floor
                dropped[s.key] = dropped.get(s.key, 0) + 1
                continue
            chosen[s.key].append(item.text)
            used += cost
    lines = [preamble]
    for s in sections:
        if chosen[s.key]:
            lines += [s.heading, *chosen[s.key]]
    return "\n".join(lines), dropped


def session_start_items(store, team: str, project: str) -> list[Item]:
    active = store.active(team, cwd=project)
    recent = store.recent_completed(team, project, limit=RECENT_WINDOW)
    goals = [t.goal for t in recent + active]
    items = [Item("active", f"- {label(t.goal, goals, MAX_GOAL_SHOWN)}"
                  + (" [escalated: ask the user how to proceed]" if t.status == "escalated" else ""))
             for t in active[:3]]
    items += [Item("pitfalls", f"- {test[:200]} (failed in {n} sessions)")
              for test, n in store.recurring_failures(project)]
    items += [Item("team_rules", f"- {r[:MAX_RULE]}") for r in store.team_rules(team)[:MAX_RULES]]
    items += [Item("constraints", f"- {c[:MAX_RULE]}")
              for c in store.project_constraints(team, project)[:MAX_CONSTRAINTS]]
    items += [Item("earlier", f"- {label(t.goal, goals)}") for t in recent[:RECENT_SHOWN]]
    return items


def progress_items(store, team: str, project: str, session: str, task) -> list[Item]:
    """What the session did before the compaction: the user's later messages, the last test run, the files it
    changed. All from the task and the event log: ids and project-relative paths, never code or output."""
    from horizon.eventlog.briefing import trace
    from horizon.eventlog.store import EventStore

    t = trace(EventStore(store.db).session(team, project, session))
    notes = [p.note for p in task.progress if not p.note.lstrip().startswith("/")][-MAX_NOTES:]
    items = [Item("progress", f"- a later message: {clip(n, 200)}") for n in notes]
    if t.last:
        failing = f"; still failing: {', '.join(t.still_failing[:4])}" if t.still_failing else ""
        items.append(Item("progress", f"- last test run: {t.last['passed']}/{t.last['total']} passing, after "
                                      f"{t.runs} runs ({t.failed_runs} failing){failing}"))
    if t.files:
        more = f" (+{len(t.files) - MAX_FILES} more)" if len(t.files) > MAX_FILES else ""
        items.append(Item("progress", f"- files changed: {', '.join(t.files[:MAX_FILES])}{more}"))
    return items


def session_start(store, team: str, project: str, preamble: str, budget: int, session: str | None = None,
                  reanchor: bool = False) -> str:
    """Lean's session-start context. `reanchor` (a start after a compaction): this session's task in full,
    first, with its progress."""
    items = session_start_items(store, team, project)
    task = next((t for t in store.active(team, cwd=project) if t.session_id == session), None) if session else None
    if not (reanchor and task):
        return compose(preamble, SESSION_START, items, budget)[0]
    items = [Item("active", f"- {clip(task.goal, MAX_GOAL_REANCHOR)}")] + [i for i in items if i.section != "active"]
    return compose(preamble, REANCHOR, items + progress_items(store, team, project, session, task), budget)[0]


def expects_trouble(log, embedder, settings, team: str, project: str, goal: str) -> bool:
    """Whether the session forecast (track C) expects at least `context_min_failed_runs` failing test runs on this
    goal. Base-rate prior only: a Jev call would add half a second to the first prompt."""
    from horizon.eventlog.forecast import SessionWorldModel

    if not log.has_episodes(team, project):
        return False
    f = SessionWorldModel(log, embedder(), settings).predict(team, project, goal)
    return f.failed_runs is not None and f.failed_runs >= settings.context_min_failed_runs
