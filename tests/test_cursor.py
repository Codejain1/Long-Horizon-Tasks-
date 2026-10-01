"""Cursor as a host: its hooks translated to Horizon's lean hooks, and the eval harness driving the Cursor CLI."""

import json
import sqlite3
import stat
import sys
import threading

from test_longhorizon import EVAL, load

from horizon.cursor import run_cursor_hook, to_cursor, to_horizon, user_messages
from horizon.db import connect
from horizon.eventlog.store import EventStore
from horizon.hooks import LEAN_WORKFLOW
from horizon.install import install_cursor
from horizon.taskstate.store import TaskStore

GOAL = "Add a refund endpoint. Money is always integer cents."
FAILING = "F.\n=== short test summary info ===\nFAILED tests/test_a.py::test_refund - assert 1 == 2\n1 failed, 1 passed in 0.01s\n"


def base(project, session="c1", **extra):
    return {"conversation_id": session, "session_id": session, "workspace_roots": [project], **extra}


def cursor_transcript(path, *messages):
    lines = [{"role": "user", "message": {"content": [
        {"type": "text", "text": f"<timestamp>now</timestamp>\n<user_query>\n{m}\n</user_query>"}]}} for m in messages]
    lines.append({"role": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}})
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return str(path)


def test_cursor_payloads_become_claude_code_shaped(project_dir):
    shell = {"tool_name": "Shell", "tool_input": {"command": "pytest -q", "cwd": "", "timeout": 30000}}
    name, ok = to_horizon("postToolUse", base(project_dir, tool_output=json.dumps({"output": "2 passed", "exitCode": 0}),
                                              **shell))
    assert name == "post-tool-use" and ok == {"session_id": "c1", "cwd": project_dir, "tool_name": "Bash",
                                              "tool_input": {"command": "pytest -q"},
                                              "tool_response": {"stdout": "2 passed", "stderr": ""}}
    _, failed = to_horizon("postToolUseFailure", base(project_dir, error_message=FAILING, **shell))
    assert failed["tool_response"]["stdout"] == FAILING  # a non-zero exit arrives as a failure
    _, edit = to_horizon("postToolUse", base(project_dir, tool_name="Write",
                                             tool_input={"file_path": f"{project_dir}/a.py", "content": "x"}))
    assert edit["tool_name"] == "Write" and edit["tool_input"] == {"file_path": f"{project_dir}/a.py"}
    assert to_horizon("afterAgentThought", base(project_dir)) is None


def test_user_messages_come_from_cursors_transcript(tmp_path):
    path = cursor_transcript(tmp_path / "t.jsonl", GOAL, "Also log refunds.")
    assert user_messages(path) == [GOAL, "Also log refunds."] and user_messages(None) == []


def test_outputs_take_cursors_shape():
    ctx = {"hookSpecificOutput": {"additionalContext": "rules"}}
    assert to_cursor("sessionStart", ctx) == {"additional_context": "rules"}
    assert to_cursor("postToolUse", None, ["task note"]) == {"additional_context": "task note"}
    assert to_cursor("preToolUse", ctx) == {"permission": "allow", "agent_message": "rules"}
    deny = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "restore first"}}
    assert to_cursor("preToolUse", deny) == {"permission": "deny", "agent_message": "restore first"}
    assert to_cursor("sessionEnd", ctx) is None


def hook(settings, event, payload):
    out = run_cursor_hook(event, json.dumps(payload), settings, profile="lean")
    return json.loads(out) if out else None


def stored(settings, project, session="c1"):
    db = connect(settings.db_url)
    try:
        return EventStore(db).session(settings.team_id, project, session), TaskStore(db).active(settings.team_id,
                                                                                               cwd=project)
    finally:
        db.close()


def test_a_cursor_session_is_captured_like_a_claude_code_one(settings, project_dir, tmp_path):
    start = hook(settings, "sessionStart", base(project_dir))
    assert LEAN_WORKFLOW in start["additional_context"]
    path = cursor_transcript(tmp_path / "t.jsonl", GOAL)
    shell = {"tool_name": "Shell", "tool_input": {"command": "python -m pytest -q"}, "transcript_path": path}
    first = hook(settings, "postToolUseFailure", base(project_dir, error_message=FAILING, **shell))
    assert "evaluate_options" not in json.dumps(first)  # Cursor has no Horizon MCP tools to call
    hook(settings, "postToolUse", base(project_dir, tool_output=json.dumps({"output": "2 passed in 0.01s",
                                                                            "exitCode": 0}), **shell))
    hook(settings, "sessionEnd", base(project_dir, reason="completed", transcript_path=path))
    events, tasks = stored(settings, project_dir)
    assert [e.kind for e in events if e.kind != "step"] == ["session_start", "prompt", "session_end"]
    runs = [e.result.metrics for e in events if e.action and e.action.name == "test_run"]
    assert runs == [{"passed": 1.0, "failed": 1.0, "total": 2.0}, {"passed": 2.0, "failed": 0.0, "total": 2.0}]
    assert [t.goal for t in tasks] == [GOAL] and tasks[0].constraints == ["Money is always integer cents."]
    again = hook(settings, "sessionStart", base(project_dir))  # `agent --resume`: the same conversation
    assert "Active task" in again["additional_context"] and GOAL in again["additional_context"]


def test_parallel_hooks_record_the_message_once(settings, project_dir, tmp_path):
    path = cursor_transcript(tmp_path / "t.jsonl", GOAL)
    payload = base(project_dir, tool_name="Read", tool_input={"file_path": "a.py"}, transcript_path=path)
    threads = [threading.Thread(target=hook, args=(settings, "postToolUse", payload)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events, tasks = stored(settings, project_dir)
    assert sum(e.kind == "prompt" for e in events) == 1 and len(tasks) == 1


def test_install_cursor_adds_horizons_hooks_and_keeps_others(tmp_path):
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "hooks.json").write_text(json.dumps(
        {"version": 1, "hooks": {"afterFileEdit": [{"command": "fmt.sh"}],  # an earlier install's preToolUse:
                                 "preToolUse": [{"command": "py -m horizon hook preToolUse --host cursor"}]}}))
    install_cursor(tmp_path, ["py", "-m", "horizon"])
    install_cursor(tmp_path, ["py", "-m", "horizon"])  # idempotent
    hooks = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())["hooks"]
    assert hooks["afterFileEdit"] == [{"command": "fmt.sh"}]
    assert hooks["sessionStart"] == [{"command": "py -m horizon hook sessionStart --host cursor --profile lean",
                                      "timeout": 30}]
    assert "preToolUse" not in hooks and len(hooks["postToolUseFailure"]) == 1
    tools = hooks["postToolUse"][0]["matcher"].split("|")
    assert {"Shell", "Write", "StrReplace"} <= set(tools) and "Read" not in tools


FAKE_AGENT = '''#!{python}
"""A fake Cursor `agent -p`: writes the reference ledger and streams one shell call."""
import json, pathlib, shutil, sys
repo = pathlib.Path.cwd()
shutil.copytree({reference!r}, repo / "ledger", dirs_exist_ok=True)
out = [{{"type": "system", "subtype": "init", "session_id": "cur-1"}},
       {{"type": "assistant", "message": {{"role": "assistant", "content": [{{"type": "text", "text": "on it"}}]}}}},
       {{"type": "tool_call", "subtype": "completed", "call_id": "t1", "tool_call": {{"shellToolCall": {{
           "args": {{"command": "python -m pytest -q"}},
           "result": {{"failure": {{"exitCode": 1, "interleavedOutput": "FAILED tests/test_l.py::test_x - no\\n1 failed in 0.1s"}}}}}}}}}},
       {{"type": "result", "subtype": "success", "result": "done", "session_id": "cur-1",
         "usage": {{"inputTokens": 100, "outputTokens": 20, "cacheReadTokens": 1000, "cacheWriteTokens": 0}}}}]
print("\\n".join(json.dumps(e) for e in out))
pathlib.Path(repo.parent / "argv.json").write_text(json.dumps(sys.argv[1:]))
'''


def test_the_harness_drives_the_cursor_cli(tmp_path):
    run = load("run")
    agent = tmp_path / "agent"
    agent.write_text(FAKE_AGENT.format(python=sys.executable, reference=str(EVAL / "reference" / "ledger")))
    agent.chmod(agent.stat().st_mode | stat.S_IEXEC)
    assert run.main(["--scenario", "ledger", "--arm", "lean", "--out", str(tmp_path / "out"), "--sessions", "1",
                     "--agent", "cursor", "--agent-bin", str(agent), "--model", "composer-2.5"]) == 0
    work = tmp_path / "out" / "ledger-lean"
    result = json.loads((work / "results.json").read_text())
    row = result["sessions"][0]
    assert result["agent"] == "cursor" and row["session_id"] == "cur-1" and row["tokens"] == 1120
    assert row["test_runs"] == 1 and row["failed_test_runs"] == 1 and row["failed_checks"] == ["tests/test_l.py::test_x"]
    assert row["hidden"]["failed"] == []
    argv = json.loads((work / "argv.json").read_text())
    assert argv[:2] == ["-p", argv[1]] and {"--force", "--trust", "--sandbox", "enabled", "composer-2.5"} <= set(argv)
    assert json.loads((work / "repo" / ".cursor" / "hooks.json").read_text())["hooks"]["sessionStart"]
    assert (work / "session1.cursor.jsonl").exists()


PEEKS = '''#!{python}
"""Like FAKE_AGENT, but it also looks outside its repo: the eval tree's hidden tests and another run's repo."""
import json, os, pathlib, shutil, sys
repo = pathlib.Path.cwd()
shutil.copytree({reference!r}, repo / "ledger", dirs_exist_ok=True)
(repo.parent / "env.json").write_text(json.dumps(dict(os.environ)))
import sqlite3
with sqlite3.connect(os.environ["HORIZON_DB_URL"].removeprefix("sqlite:///")) as db:  # what a hook would write
    db.execute("CREATE TABLE IF NOT EXISTS events (n INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, data TEXT)")
    db.execute("INSERT INTO events (session_id, kind, data) VALUES ('cur-1', 'session_start', '{{}}')")
calls = [{{"readToolCall": {{"args": {{"path": {hidden!r}}}}}}},
         {{"globToolCall": {{"args": {{"targetDirectory": str(repo), "globPattern": "*.py"}}}}}},
         {{"lsToolCall": {{"args": {{"path": str(repo.parent)}}}}}},
         {{"globToolCall": {{"args": {{"targetDirectory": str(pathlib.Path.home() / ".cursor/projects/T-lhx-x-repo/agent-transcripts")}}}}}},
         {{"shellToolCall": {{"args": {{"command": os.path.realpath(sys.executable) + " -m pytest -q"}}}}}},
         {{"shellToolCall": {{"args": {{"command": "cat /tmp/lhx-other/repo/ledger/core.py"}}}}}}]
out = [{{"type": "system", "subtype": "init", "session_id": "cur-1"}}]
out += [{{"type": "tool_call", "subtype": "started", "call_id": f"t{{i}}", "tool_call": c}} for i, c in enumerate(calls)]
out += [{{"type": "result", "subtype": "success", "result": "done", "session_id": "cur-1", "usage": {{}}}}]
print("\\n".join(json.dumps(e) for e in out))
'''


def test_an_isolated_run_keeps_the_agent_out_of_the_eval_tree_and_counts_its_peeks(tmp_path, monkeypatch):
    run = load("run")
    monkeypatch.setattr(run, "agent_python", lambda: sys.executable)  # the real one builds a cached env
    monkeypatch.setattr(run.tempfile, "tempdir", str(tmp_path / "t"))
    (tmp_path / "t").mkdir()
    agent = tmp_path / "agent"
    agent.write_text(PEEKS.format(python=sys.executable, reference=str(EVAL / "reference" / "ledger"),
                                  hidden=str(EVAL / "scenarios" / "ledger" / "hidden")))
    agent.chmod(agent.stat().st_mode | stat.S_IEXEC)
    assert run.main(["--scenario", "ledger", "--arm", "lean", "--out", str(tmp_path / "out"), "--sessions", "1",
                     "--agent", "cursor", "--agent-bin", str(agent), "--isolate"]) == 0
    work = tmp_path / "out" / "ledger-lean"
    repo = (work / "repo").resolve()
    assert (work / "repo").is_symlink() and repo.parent.name.startswith("lhx-") and work not in repo.parents
    row = json.loads((work / "results.json").read_text())["sessions"][0]
    assert row["outside_reads"] == {"eval": 1, "other_runs": 1, "agent_memory": 1, "elsewhere": 0}
    seen = json.loads((repo.parent / "env.json").read_text())
    root = str(run.REPO_ROOT)
    assert not [k for k, v in seen.items() if root in v and k != "PATH"]
    assert seen["HORIZON_DB_URL"].endswith("/horizon.db") and str(work) not in seen["HORIZON_DB_URL"]
    with sqlite3.connect(work / "horizon.db") as conn:  # the isolated database, copied into the run folder
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert row["horizon"]["events"] == {"session_start": 1} and len((work / "events.jsonl").read_text().split()) == 1


def test_the_agents_environment_names_nothing_in_this_repo(monkeypatch):
    run = load("run")
    root = str(run.REPO_ROOT)
    monkeypatch.setenv("PATH", f"{root}/.venv/bin:/usr/bin")
    monkeypatch.setenv("VIRTUAL_ENV", f"{root}/.venv")
    monkeypatch.setenv("PWD", root)
    env = run.agent_env("/opt/horizon-env/bin/python")
    assert env["PATH"] == "/opt/horizon-env/bin:/usr/bin"
    assert "VIRTUAL_ENV" not in env and "PWD" not in env


HANGS_ONCE = '''#!{python}
"""Like FAKE_AGENT, but the first call leaves a stray file and hangs, as Cursor does when stuck reconnecting."""
import pathlib, subprocess, sys, time
marker = pathlib.Path.cwd().parent / "hung"
if not marker.exists():
    marker.write_text("1")
    (pathlib.Path.cwd() / "half_done.py").write_text("x = 1")
    print('{{"type": "connection", "subtype": "reconnecting"}}', flush=True)
    time.sleep(30)
sys.exit(subprocess.run([sys.executable, {fake!r}, *sys.argv[1:]]).returncode)
'''


def test_the_harness_retries_a_dropped_session_from_the_last_commit(tmp_path, monkeypatch):
    run = load("run")
    monkeypatch.setattr(run, "SESSION_TIMEOUT", 3)
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE_AGENT.format(python=sys.executable, reference=str(EVAL / "reference" / "ledger")))
    agent = tmp_path / "agent"
    agent.write_text(HANGS_ONCE.format(python=sys.executable, fake=str(fake)))
    agent.chmod(agent.stat().st_mode | stat.S_IEXEC)
    assert run.main(["--scenario", "ledger", "--arm", "baseline", "--out", str(tmp_path / "out"), "--sessions", "1",
                     "--agent", "cursor", "--agent-bin", str(agent), "--model", "composer-2.5"]) == 0
    work = tmp_path / "out" / "ledger-baseline"
    row = json.loads((work / "results.json").read_text())["sessions"][0]
    assert row["retries"] == 1 and row["aborted"] is None and row["session_id"] == "cur-1"
    assert row["hidden"]["failed"] == [] and not (work / "repo" / "half_done.py").exists()
