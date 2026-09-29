"""The lean profile: task continuity only (goals, constraints, team rules), one outcome per task."""

import dataclasses
import json

import pytest

from horizon.hooks import LEAN_WORKFLOW, WORKFLOW, run_hook
from horizon.install import install
from horizon.service import Platform


def test_install_lean_writes_the_lean_snippet_env_and_hook_flag(tmp_path):
    install(tmp_path, command=["py", "-m", "horizon"], profile="lean")
    assert "task continuity" in (tmp_path / "CLAUDE.md").read_text()
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["horizon"]["env"] == {"HORIZON_PROFILE": "lean"}
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    assert all(g["hooks"][0]["command"].endswith("--profile lean") for groups in hooks.values() for g in groups)
    install(tmp_path, command=["py", "-m", "horizon"])  # back to full: replaces, doesn't duplicate
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    assert [len(groups) for groups in hooks.values()] == [1, 1, 1, 1]
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


def test_lean_hooks_swap_the_workflow_and_skip_per_run_nudges(settings, project_dir):
    ctx = json.loads(run_hook("session-start", json.dumps({"cwd": project_dir}), settings, profile="lean"))
    text = ctx["hookSpecificOutput"]["additionalContext"]
    assert LEAN_WORKFLOW in text and WORKFLOW not in text
    bash = {"session_id": "s", "cwd": project_dir, "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
            "tool_response": {"stdout": "==== 3 passed in 0.1s ====", "stderr": ""}}
    assert run_hook("post-tool-use", json.dumps(bash), settings, profile="lean") == ""
    assert run_hook("post-tool-use", json.dumps(bash), settings) != ""
