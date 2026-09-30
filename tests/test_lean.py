"""The lean profile: task continuity only (goals, constraints, team rules), one outcome per task."""

import dataclasses
import json
from pathlib import Path

import pytest

from horizon.hooks import LEAN_WORKFLOW, WORKFLOW, run_hook
from horizon.install import install
from horizon.service import Platform


def test_install_lean_writes_the_lean_snippet_env_and_hooks(tmp_path):
    install(tmp_path, command=["py", "-m", "horizon"], profile="lean")
    assert "You don't need to call it" in (tmp_path / "CLAUDE.md").read_text()
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["horizon"]["env"] == {"HORIZON_PROFILE": "lean"}
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    assert "UserPromptSubmit" in hooks
    assert all(g["hooks"][0]["command"].endswith("--profile lean") for groups in hooks.values() for g in groups)
    install(tmp_path, command=["py", "-m", "horizon"])  # back to full: replaces, doesn't duplicate
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    assert "UserPromptSubmit" not in hooks and [len(groups) for groups in hooks.values()] == [1, 1, 1, 1]
    assert "--profile" not in json.dumps(hooks) and "env" not in (tmp_path / ".mcp.json").read_text()
    with pytest.raises(SystemExit):
        install(tmp_path, hosted="https://h.example", profile="lean")


def test_lean_task_records_its_one_outcome(settings, task_store, memrouter, project_dir):
    """No baseline run: the first record_outcome is the task's outcome, not swallowed as a baseline."""
    lean = Platform(dataclasses.replace(settings, profile="lean"), task_store, lambda: memrouter, cwd=project_dir)
    started = lean.start_task("Build payouts", team_rules=["ids are uuid4 hex"])
    assert "record_outcome once" in started["next"] and started["team_rules"] == ["ids are uuid4 hex"]
    out = lean.record_outcome(started["task_id"], "implemented payouts", "stdlib package",
                              tests_passed=5, tests_failed=0, task_complete=True)
    assert out["task_status"] == "completed" and out["actual_success"] == 1


def lean(name, payload, settings):
    out = run_hook(name, json.dumps(payload), settings, profile="lean")
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None


TEAM_PROMPT = ("New project: a package `invoices`. Our team's rules for every service we build, now and in future "
               "projects: ids are `uuid.uuid4().hex`; money is integer cents. Money must never be a float. "
               "Implement `create_invoice`.\n```python\nSECRET = 1\n```")


def test_lean_captures_the_task_and_rules_without_tool_calls(settings, task_store, project_dir):
    """The agent calls nothing: the first prompt becomes the task (code removed), rule sentences become team
    rules or constraints, and the next session, here or in another project, is shown them."""
    ctx = lean("session-start", {"cwd": project_dir, "session_id": "s1"}, settings)
    assert LEAN_WORKFLOW in ctx and WORKFLOW not in ctx
    assert lean("user-prompt", {"cwd": project_dir, "session_id": "s1", "prompt": TEAM_PROMPT}, settings) is None
    lean("user-prompt", {"cwd": project_dir, "session_id": "s1", "prompt": "Also add a due date."}, settings)
    [task] = task_store.active(settings.team_id, cwd=project_dir)
    assert task.goal.startswith("New project") and "SECRET" not in task.goal
    assert task.constraints == ["Money must never be a float."]
    assert task.team_rules[0].startswith("Our team's rules") and "uuid4().hex" in task.team_rules[0]
    assert [p.note for p in task.progress] == ["Also add a due date."]

    # Next session, same project: the task is done and shows as earlier work, with its constraint.
    ctx = lean("session-start", {"cwd": project_dir, "session_id": "s2"}, settings)
    assert task_store.active(settings.team_id, cwd=project_dir) == []
    assert "constraint: Money must never be a float." in ctx and "Our team's rules" in ctx

    # Another project of the team: the team rules, not this project's constraints.
    other = str(Path(project_dir).parent / "other")
    ctx = lean("session-start", {"cwd": other, "session_id": "s3"}, settings)
    assert "uuid4().hex" in ctx and "Money must never" not in ctx


def test_lean_resumed_session_keeps_its_task_and_never_blocks(settings, task_store, project_dir):
    lean("user-prompt", {"cwd": project_dir, "session_id": "s1", "prompt": "Build payouts."}, settings)
    lean("session-start", {"cwd": project_dir, "session_id": "s1", "source": "compact"}, settings)
    assert len(task_store.active(settings.team_id, cwd=project_dir)) == 1
    bash = {"session_id": "s1", "cwd": project_dir, "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
            "tool_response": {"stdout": "==== 3 passed in 0.1s ====", "stderr": ""}}
    assert run_hook("post-tool-use", json.dumps(bash), settings, profile="lean") == ""
    assert len(task_store.pending_captures(project_dir)) == 1  # still captured
    assert run_hook("stop", json.dumps({"cwd": project_dir, "session_id": "s1"}), settings, profile="lean") == ""
    assert run_hook("stop", json.dumps({"cwd": project_dir, "session_id": "s1"}), settings) != ""  # full blocks


def test_recurring_failures_warn_the_next_session(settings, task_store, project_dir):
    """A check that failed in two earlier sessions is a pitfall; one that failed once is test-first work."""
    out = "FAILED tests/test_policy.py::test_never_prints - assert\n==== 1 failed, 2 passed in 0.1s ===="
    for session in ("s1", "s2"):
        bash = {"session_id": session, "cwd": project_dir, "tool_name": "Bash", "tool_input": {"command": "pytest"},
                "tool_response": {"stdout": out, "stderr": ""}}
        run_hook("post-tool-use", json.dumps(bash), settings, profile="lean")
    once = out.replace("test_policy.py::test_never_prints", "test_new.py::test_restock")
    run_hook("post-tool-use", json.dumps({**bash, "session_id": "s3", "tool_response": {"stdout": once}}), settings)
    ctx = lean("session-start", {"cwd": project_dir, "session_id": "s4"}, settings)
    assert "tests/test_policy.py::test_never_prints (failed in 2 sessions)" in ctx and "test_restock" not in ctx
