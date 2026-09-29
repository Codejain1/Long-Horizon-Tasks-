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


def test_session_start_carries_finished_tasks_constraints(platform, settings, task_store, project_dir):
    """Multi-session work: a later session sees the goal and constraints of the session that finished."""
    task_id = platform.start_task("Build the ledger", constraints=["amounts are integer cents"])["task_id"]
    task = task_store.get(task_id, settings.team_id)
    task.status = "completed"
    task_store.save(task)
    ctx = hook("session-start", {"cwd": project_dir}, settings)["hookSpecificOutput"]["additionalContext"]
    assert "Active tasks" not in ctx
    assert "Build the ledger" in ctx and "constraint: amounts are integer cents" in ctx


def test_team_rules_reach_every_project(platform, settings, project_dir, tmp_path):
    """Rules the user set for all projects show up at session start in a different project, and the latest
    task that sets them is the current set."""
    platform.start_task("Build invoices", team_rules=["ids are uuid4 hex", "stamps end in Z"], cwd="/elsewhere")
    ctx = hook("session-start", {"cwd": project_dir}, settings)["hookSpecificOutput"]["additionalContext"]
    assert "Team rules" in ctx and "- ids are uuid4 hex" in ctx and "Build invoices" not in ctx
    started = platform.start_task("Build payouts")
    assert started["team_rules"] == ["ids are uuid4 hex", "stamps end in Z"]
    platform.start_task("Build refunds", team_rules=["stamps end in Z"])
    ctx = hook("session-start", {"cwd": project_dir}, settings)["hookSpecificOutput"]["additionalContext"]
    assert "stamps end in Z" in ctx and "uuid4" not in ctx

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


def test_stop_is_quiet_unless_an_outcome_is_unrecorded(platform, settings, project_dir):
    # No task, no tests: silent.
    assert hook("stop", {"cwd": project_dir, "session_id": "s1"}, settings) is None
    # Tests ran but there is no active task: still silent (nothing to record against).
    hook("post-tool-use", bash_payload(project_dir), settings)
    assert hook("stop", {"cwd": project_dir, "session_id": "s1"}, settings) is None
    # A task started after that run doesn't inherit it.
    platform.start_task("x")
    assert hook("stop", {"cwd": project_dir, "session_id": "s1"}, settings) is None


def test_stop_blocks_once_per_unrecorded_outcome(platform, settings, project_dir):
    task_id = platform.start_task("x")["task_id"]
    assert hook("stop", {"cwd": project_dir, "session_id": "s1"}, settings) is None  # nothing pending

    hook("post-tool-use", bash_payload(project_dir), settings)
    out = hook("stop", {"cwd": project_dir, "session_id": "s1", "stop_hook_active": False}, settings)
    assert out["decision"] == "block" and task_id in out["reason"] and "Unrecorded outcome" in out["reason"]
    # Never loop within a turn, and never nag twice about the same test run.
    assert hook("stop", {"cwd": project_dir, "session_id": "s1", "stop_hook_active": True}, settings) is None
    assert hook("stop", {"cwd": project_dir, "session_id": "s1"}, settings) is None

    # A new unrecorded test run gets one new nudge; recording clears it.
    hook("post-tool-use", bash_payload(project_dir), settings)
    platform.record_outcome(task_id, "s", "c")
    assert hook("stop", {"cwd": project_dir, "session_id": "s1"}, settings) is None


def test_stop_ignores_test_runs_from_other_sessions(platform, settings, project_dir):
    platform.start_task("x")
    hook("post-tool-use", bash_payload(project_dir), settings)  # session s1
    assert hook("stop", {"cwd": project_dir, "session_id": "s2"}, settings) is None
    assert hook("stop", {"cwd": project_dir, "session_id": "s1"}, settings)["decision"] == "block"


def test_migrates_captures_table_from_first_phase2_schema(tmp_path, settings):
    import dataclasses
    import sqlite3

    from horizon.taskstate.store import TaskStore
    from horizon.db import connect

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE test_captures (id TEXT PRIMARY KEY, session_id TEXT, cwd TEXT, command TEXT NOT NULL,
        runner TEXT NOT NULL, passed INTEGER NOT NULL, failed INTEGER NOT NULL, total INTEGER NOT NULL,
        created_at TEXT NOT NULL, consumed_by TEXT)""")
    con.commit()
    con.close()
    store = TaskStore(connect(f"sqlite:///{path}"))
    old = dataclasses.replace(settings, db_url=f"sqlite:///{path}")
    hook("post-tool-use", bash_payload("/p"), old)
    assert store.pending_captures("/p")[0].nudged_at is None


def test_claude_project_dir_wins_over_cwd(platform, settings, project_dir, monkeypatch, tmp_path):
    platform.start_task("x")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", project_dir)
    hook("post-tool-use", bash_payload(str(tmp_path)), settings)  # e.g. after `cd sub/`
    assert hook("stop", {"cwd": str(tmp_path), "session_id": "s1"}, settings)["decision"] == "block"


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


def test_stored_test_commands_carry_no_inline_code():
    from horizon.hooks import command_summary

    assert command_summary('python -c "import os; os.remove(p)" && pytest -q') == 'python -c "…" && pytest -q'
    assert command_summary("python - <<EOF\nimport secret_module\nEOF") == "python - <<EOF"
    assert command_summary("pytest -q tests/test_api.py") == "pytest -q tests/test_api.py"


def test_hosted_hooks_send_parsed_facts_only(monkeypatch, tmp_path):
    """Hosted mode: no test output, no code and no paths leave the machine; the project is a hash."""
    from horizon.hooks import Remote, project_key

    sent = []
    monkeypatch.setattr(Remote, "_post", lambda self, op, body: sent.append((op, body)) or None)
    monkeypatch.setenv("HORIZON_API_KEY", "hzn_test")
    output = "FAILED tests/test_a.py::test_x - AssertionError: secret_value=42\n==== 1 failed, 3 passed in 0.1s ===="
    payload = {"session_id": "s1", "cwd": str(tmp_path), "tool_name": "Bash",
               "tool_input": {"command": 'python -c "import app; app.run()" && pytest -q'},
               "tool_response": {"stdout": output, "stderr": "Traceback ... private"}}
    run_hook("post-tool-use", json.dumps(payload), remote="https://horizon.example.com")
    [(op, body)] = sent
    assert op == "capture" and body["project"] == project_key(str(tmp_path)) and body["project"].startswith("prj_")
    assert body["capture"] == {"command": 'python -c "…" && pytest -q', "runner": "pytest", "passed": 3,
                               "failed": 1, "failing": ["tests/test_a.py::test_x"]}
    wire = json.dumps(sent)
    assert "secret_value" not in wire and "Traceback" not in wire and str(tmp_path) not in wire


def test_hosted_hook_api_needs_a_team_key(settings):
    import dataclasses

    from fastapi.testclient import TestClient

    from horizon.server import http_app

    api = TestClient(http_app(dataclasses.replace(settings)))
    assert api.post("/api/hooks/session-start", json={"project": "prj_x"}).status_code == 401
    assert api.post("/api/hooks/session-start", json={"project": "prj_x"},
                    headers={"Authorization": "Bearer hzn_nope"}).status_code == 401


def test_hosted_teams_with_the_same_project_path_stay_isolated(settings):
    """Two teams' users with the same path hash to the same project key: their hooks must not mix."""
    import dataclasses
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from horizon.accounts import Accounts
    from horizon.db import connect
    from horizon.server import hosted_scope, http_app
    from horizon.taskstate.store import TaskStore

    app = http_app(dataclasses.replace(settings))
    api = TestClient(app)
    accounts = Accounts(connect(settings.db_url))
    (a, key_a), (b, key_b) = accounts.create_team("A"), accounts.create_team("B")
    cap = {"command": "pytest", "runner": "pytest", "passed": 1, "failed": 2, "failing": ["t::x", "t::y"]}
    api.post("/api/hooks/capture", json={"project": "prj_same", "session_id": "s", "capture": cap},
             headers={"Authorization": f"Bearer {key_a}"})
    store = TaskStore(connect(settings.db_url))
    assert len(store.pending_captures(hosted_scope(a, "prj_same"))) == 1
    assert store.pending_captures(hosted_scope(b, "prj_same")) == []
    ckpt = {"id": "ckpt_x", "cwd": hosted_scope(b, "prj_same"), "git": {"repo": ".", "commit": "abc"}}
    api.post("/api/hooks/checkpoint", json={"checkpoint": ckpt}, headers={"Authorization": f"Bearer {key_a}"})
    # A client naming another team's scope gets it re-scoped under its own team.
    assert store.take_checkpoint(hosted_scope(b, "prj_same"), datetime(2000, 1, 1, tzinfo=UTC), "t") is None
    assert store.take_checkpoint(f"{a}/{b}/prj_same", datetime(2000, 1, 1, tzinfo=UTC), "t") is not None
