"""Automatic decisions (`HORIZON_DECIDE=on`, lean): Horizon, not the agent, decides when a choice needs Jev.

When a task starts, Jev reads its message: does it involve a crucial, hard-to-change technical choice? If so, the
agent is told to put its options through `evaluate_options` before writing code, and its first edit without an
evaluation is denied once with the same instruction. The agent still names the options (it has read the code);
Horizon scores them (success, compatibility, future fit, efficiency) and, on a close call, sends it to the
consequence checks (the world model's forecast, then spikes).
"""

from __future__ import annotations

from horizon.decision.scorers import noul

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
THRESHOLD = 0.5

# Reworded after the first boxoffice run, where tickets that fixed the function names but left the representation
# open (money 0.32, booking references 0.43) went under. A false alarm costs one evaluation; a miss, the point.
QUESTION = noul({
    "question": "Will doing the task in `message` force the developer to choose how something is represented or "
                "built that later work will depend on and that would be costly to change afterwards, even if the "
                "message already fixes the function names and inputs?",
    "counts": "How data is stored or persisted; how money, amounts, time or units are represented; how ids or "
              "references are generated; concurrency or process model; a framework, key library or external "
              "service; the shape of data other code or other systems will rely on.",
    "not_counted": "A small feature built on choices already made, a query or listing over existing data, bug "
                   "fixes, renames, tests or docs, or a task whose message already prescribes the representation.",
})


SETTLED = noul("Is the choice the task in `message` needs already made by one of `earlier_decisions` (the same "
               "kind of choice for the same part of the system), so it only has to follow it?")


def earlier_decisions(tasks) -> list[str]:
    """The project's decisions so far, as "situation -> chosen" lines."""
    return [f"{t.last_evaluation.get('situation', '')[:200]} -> {t.last_evaluation.get('chosen')}"
            for t in tasks if t.last_evaluation and t.last_evaluation.get("chosen")][:20]


def crucial_ahead(scorer, message: str, earlier: list[str] | None = None) -> bool:
    """Jev's read of whether a task's message hides a crucial choice not already settled by an earlier decision
    (seen on Haiku: tickets 6 and 8 re-ran the storage decision ticket 2 had made). False without a scorer."""
    if scorer is None or not message.strip():
        return False
    questions = {"crucial": QUESTION, **({"settled": SETTLED} if earlier else {})}
    try:
        a = scorer.ask({"message": message, "earlier_decisions": earlier or []}, questions)
    except Exception:  # the scorer is down: no trigger, the task goes on as usual
        return False
    return a["crucial"].value >= THRESHOLD and (not earlier or a["settled"].value < THRESHOLD)


def instruction(task_id: str) -> str:
    return (f"Horizon: this task involves a crucial choice that later work will build on. Before writing code, list "
            f"2-4 realistic approaches, including the one you would pick, and call evaluate_options (task_id "
            f"{task_id}) with them and your pick as `preferred`. If Horizon agrees, go ahead; otherwise follow its "
            "decision: implement `chosen`, or run its consequence plan and call submit_consequences on a close "
            "call.")


def edit_check(task, tool: str) -> str | None:
    """The denial reason for the first edit of a task whose crucial choice wasn't evaluated, once per task."""
    if tool not in EDIT_TOOLS or not task.decision_due or task.last_evaluation or task.decision_nudged:
        return None
    task.decision_nudged = True
    return instruction(task.id)
