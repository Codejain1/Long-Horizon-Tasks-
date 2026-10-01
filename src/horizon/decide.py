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

QUESTION = noul({
    "question": "Does the task in `message` require choosing between technical approaches that will be hard or "
                "expensive to change once later work builds on them?",
    "counts": "How data is stored or persisted, the data model or identifiers, how money, time or units are "
              "represented, concurrency or process model, a framework, key library or external service, or the "
              "shape of a public interface other code will depend on.",
    "not_counted": "Routine features on top of choices already made, bug fixes, renames, tests, docs, or an "
                   "approach the message already prescribes.",
})


def crucial_ahead(scorer, message: str) -> bool:
    """Jev's read of whether a task's message hides a crucial choice; False when there is no scorer."""
    if scorer is None or not message.strip():
        return False
    try:
        return scorer.ask({"message": message}, {"crucial": QUESTION})["crucial"].value >= THRESHOLD
    except Exception:  # the scorer is down: no trigger, the task goes on as usual
        return False


def instruction(task_id: str) -> str:
    return (f"Horizon: this task involves a crucial choice that later work will build on. Before writing code, list "
            f"2-4 realistic approaches and call evaluate_options (task_id {task_id}) with them, then follow its "
            "decision: implement `chosen`, or run its consequence plan and call submit_consequences on a close "
            "call.")


def edit_check(task, tool: str) -> str | None:
    """The denial reason for the first edit of a task whose crucial choice wasn't evaluated, once per task."""
    if tool not in EDIT_TOOLS or not task.decision_due or task.last_evaluation or task.decision_nudged:
        return None
    task.decision_nudged = True
    return instruction(task.id)
