import json
import os
import subprocess
import sys

import pytest

from horizon.hooks import run_hook

PYTEST_OUT = "collected 4 items\n\n==== 1 failed, 3 passed in 0.10s ===="


@pytest.fixture(autouse=True)
def no_project_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def bash_payload(cwd, command="pytest -q", stdout=PYTEST_OUT, **extra):
    return {"session_id": "s1", "cwd": cwd, "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_response": {"stdout": stdout, "stderr": ""}, **extra}


def hook(name, payload, settings):
    out = run_hook(name, json.dumps(payload), settings)
    return json.loads(out) if out else None


def test_session_start_injects_workflow_and_active_task(platform, settings, project_dir):
    out = hook("session-start", {"cwd": project_dir, "source": "startup"}, settings)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "start_task" in ctx and "record_outcome" in ctx
    assert "Active tasks" not in ctx

    task_id = platform.start_task("Ship the export feature")["task_id"]
    ctx = hook("session-start", {"cwd": project_dir}, settings)["hookSpecificOutput"]["additionalContext"]
    assert task_id in ctx and "Ship the export feature" in ctx


def test_post_tool_use_captures_counts_not_output(platform, settings, task_store, project_dir):
    platform.start_task("x")
    out = hook("post-tool-use", bash_payload(project_dir), settings)
    assert "1 failed" in out["hookSpecificOutput"]["additionalContext"]
    [cap] = task_store.pending_captures(project_dir)
    assert (cap.passed, cap.failed, cap.total, cap.runner) == (3, 1, 4, "pytest")
    # Only counts and the command are stored; never the raw output.
    assert "collected" not in cap.model_dump_json()


def test_post_tool_use_ignores_non_tests_and_unparseable(settings, task_store, project_dir):
    assert hook("post-tool-use", bash_payload(project_dir, command="ls"), settings) is None
    assert hook("post-tool-use", bash_payload(project_dir, stdout="bash: pytest: not found"), settings) is None
    assert hook("post-tool-use", {**bash_payload(project_dir), "tool_name": "Edit"}, settings) is None
    assert task_store.pending_captures(project_dir) == []


def test_post_tool_use_without_task_asks_for_start_task(settings, project_dir):
    out = hook("post-tool-use", bash_payload(project_dir), settings)
    assert "start_task" in out["hookSpecificOutput"]["additionalContext"]


def test_stop_blocks_once_until_outcome_recorded(platform, settings, project_dir):
    task_id = platform.start_task("x")["task_id"]
    assert hook("stop", {"cwd": project_dir}, settings) is None  # nothing pending

    hook("post-tool-use", bash_payload(project_dir), settings)
    out = hook("stop", {"cwd": project_dir, "stop_hook_active": False}, settings)
    assert out["decision"] == "block" and task_id in out["reason"]
    # Never loop: once Claude is continuing because of this hook, let it stop.
    assert hook("stop", {"cwd": project_dir, "stop_hook_active": True}, settings) is None

    platform.record_outcome(task_id, "s", "c")
    assert hook("stop", {"cwd": project_dir}, settings) is None


def test_claude_project_dir_wins_over_cwd(platform, settings, project_dir, monkeypatch, tmp_path):
    platform.start_task("x")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", project_dir)
    hook("post-tool-use", bash_payload(str(tmp_path)), settings)  # e.g. after `cd sub/`
    assert hook("stop", {"cwd": str(tmp_path)}, settings)["decision"] == "block"


def test_hooks_never_raise(settings):
    assert run_hook("stop", "not json", settings) == ""
    assert run_hook("post-tool-use", json.dumps({"tool_name": "Bash", "tool_input": None}), settings) == ""


def test_cli_hook_subprocess(settings, project_dir, tmp_path):
    env = {**os.environ, "HORIZON_DB_URL": settings.db_url}
    env.pop("CLAUDE_PROJECT_DIR", None)
    proc = subprocess.run([sys.executable, "-m", "horizon", "hook", "session-start"],
                          input=json.dumps({"cwd": project_dir}), capture_output=True, text=True, env=env,
                          timeout=60)
    assert proc.returncode == 0
    assert "start_task" in json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
