"""HORIZON_DECIDE: Horizon triggers evaluate_options itself on tasks with a crucial choice."""

import json

from horizon.decision.scorers import Answer
from horizon.hooks import Local, lean_pre_tool_use, lean_user_prompt


class Crucial:
    """A stand-in for Jev: the message is crucial when it mentions storage; settled when an earlier decision
    was about storage too."""

    def ask(self, state, questions):
        out = {"crucial": Answer(0.9 if "storage" in state["message"] else 0.1)}
        if "settled" in questions:
            out["settled"] = Answer(0.9 if any("storage" in d for d in state["earlier_decisions"]) else 0.1)
        return out


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


def test_a_choice_an_earlier_task_settled_doesnt_trigger_again(settings, task_store, project_dir):
    lean_user_prompt(task_store, settings.team_id, project_dir, "s1", "Ticket 2: choose the storage.",
                     scorer=Crucial(), decide=True)
    [task] = task_store.active(settings.team_id, cwd=project_dir)
    task.last_evaluation = {"situation": "how to persist the storage", "chosen": "SQLite"}
    task.status = "completed"
    task_store.save(task)
    out = lean_user_prompt(task_store, settings.team_id, project_dir, "s2", "Ticket 6: the storage now has more "
                           "users.", scorer=Crucial(), decide=True)
    assert "crucial choice" not in json.dumps(out)


def test_decision_history_carries_what_happened_after_a_choice_in_another_project(settings, task_store, tmp_path):
    """Project A chose a JSON file, then an incident followed and a concurrency check failed; project B's
    decision sees both. Its own project's decisions aren't repeated there."""
    from horizon.models import TaskState, TestCapture

    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    first = task_store.create(TaskState(team_id=settings.team_id, goal="Store notes on disk", cwd=a))
    first.last_evaluation = {"situation": "how to persist notes", "chosen": "JSON file"}
    task_store.save(first)
    task_store.create(TaskState(team_id=settings.team_id, goal="Incident: notes lost with 4 workers", cwd=a))
    task_store.add_capture(TestCapture(session_id="s", cwd=a, command="pytest", runner="pytest", passed=3, failed=1,
                                       total=4, failing=["tests/test_store.py::test_concurrent_writers"]))
    task_store.create(TaskState(team_id=settings.team_id, goal="Track stock", cwd=b))
    [h] = task_store.decision_history(settings.team_id, exclude_cwd=b)
    assert h["chosen"] == "JSON file" and "Incident: notes lost with 4 workers" in h["later_tasks"]
    assert h["later_failures"] == ["tests/test_store.py::test_concurrent_writers"]
    assert task_store.decision_history(settings.team_id, exclude_cwd=a) == []
