"""HORIZON_DECIDE: Horizon triggers evaluate_options itself on tasks with a crucial choice."""

import json

from horizon.decision.scorers import Answer
from horizon.hooks import Local, lean_pre_tool_use, lean_user_prompt


class Crucial:
    """A stand-in for Jev: the message is crucial when it mentions storage."""

    def ask(self, state, questions):
        return {"crucial": Answer(0.9 if "storage" in state["message"] else 0.1)}


def edit(backend, project, session="s1", tool="Write"):
    return lean_pre_tool_use({"tool_name": tool, "session_id": session, "tool_input": {}}, backend, project)


def test_a_crucial_task_asks_for_an_evaluation_and_holds_the_first_edit_once(settings, task_store, project_dir):
    backend = Local(task_store, settings.team_id, lambda: Crucial(), decide=True)
    out = lean_user_prompt(task_store, settings.team_id, project_dir, "s1", "Ticket 2: choose the storage.",
                           scorer=Crucial(), decide=True)
    [task] = task_store.active(settings.team_id, cwd=project_dir)
    text = out["hookSpecificOutput"]["additionalContext"]
    assert task.decision_due and "evaluate_options" in text and task.id in text
    denied = edit(backend, project_dir)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "evaluate_options" in denied["hookSpecificOutput"]["permissionDecisionReason"]
    assert edit(backend, project_dir) is None  # once only: never a loop
    assert edit(backend, project_dir, tool="Read") is None


def test_an_evaluated_or_routine_task_edits_freely(settings, task_store, project_dir):
    backend = Local(task_store, settings.team_id, lambda: Crucial(), decide=True)
    lean_user_prompt(task_store, settings.team_id, project_dir, "s1", "Ticket 2: choose the storage.",
                     scorer=Crucial(), decide=True)
    [task] = task_store.active(settings.team_id, cwd=project_dir)
    task.last_evaluation = {"decision_id": "d1"}
    task_store.save(task)
    assert edit(backend, project_dir) is None
    out = lean_user_prompt(task_store, settings.team_id, project_dir, "s2", "Ticket 3: list the events.",
                           scorer=Crucial(), decide=True)
    assert "crucial choice" not in json.dumps(out) and edit(backend, project_dir, session="s2") is None


def test_off_by_default_and_without_a_scorer(settings, task_store, project_dir):
    for scorer, decide in ((Crucial(), False), (None, True)):
        out = lean_user_prompt(task_store, settings.team_id, project_dir, f"s{decide}", "choose the storage",
                               scorer=scorer, decide=decide)
        assert "crucial choice" not in json.dumps(out)
    assert not any(t.decision_due for t in task_store.active(settings.team_id, cwd=project_dir))
