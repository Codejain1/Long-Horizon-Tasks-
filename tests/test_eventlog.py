"""v2 Phase 0: the event log, the adapter interface, the coding adapter and replay into transitions."""

import json
from pathlib import Path

import pytest

from horizon.adapters import CodingAdapter, get_adapter
from horizon.adapters.coding import classify_command, classify_path, events_from_hook, relative_path
from horizon.db import connect
from horizon.eventlog import Action, Event, EventStore, Result
from horizon.eventlog.transitions import replay
from horizon.hooks import run_hook
from horizon.install import install
from horizon.taskstate.usage import turn_usage

TEAM = "local"


@pytest.fixture
def events(settings):
    db = connect(settings.db_url)
    yield EventStore(db)
    db.close()


def hook_events(project, name, payload, usage=None):
    return events_from_hook(name, {"session_id": "s1", "cwd": project, **payload}, team_id=TEAM, project=project,
                            project_dir=project, usage=usage)


# --- the schema and the store -------------------------------------------------------------------------

def test_the_core_schema_has_no_domain_specific_fields():
    """The core stays general: a domain speaks through Action.name, Result.metrics/facts and data."""
    assert set(Event.model_fields) == {"id", "schema_version", "team_id", "project", "session_id", "domain", "source",
                                       "kind", "text", "action", "result", "usage", "data", "at"}
    assert set(Action.model_fields) == {"name", "target", "risky", "risk"}
    assert set(Result.model_fields) == {"ok", "metrics", "facts"}
    assert Event(team_id=TEAM, project="p", domain="browser", source="sdk", kind="step",
                 action=Action(name="click", target="#buy"), result=Result(ok=True)).schema_version == 1


def test_events_round_trip_in_write_order_per_session(events):
    a = [Event(team_id=TEAM, project="p", session_id="s1", domain="coding", source="claude-code", kind=k)
         for k in ("session_start", "prompt", "turn_end")]
    b = Event(team_id=TEAM, project="p", session_id="s2", domain="coding", source="claude-code", kind="prompt",
              text="Add a CSV export.")
    other_team = Event(team_id="other", project="p", session_id="s1", domain="coding", source="x", kind="prompt")
    same_time = a[0].at
    for e in a:
        events.append(e.model_copy(update={"at": same_time}))  # equal timestamps: insertion order decides
    events.append(b)
    events.append(other_team)
    assert [e.kind for e in events.session(TEAM, "p", "s1")] == ["session_start", "prompt", "turn_end"]
    assert events.session(TEAM, "p", "s2")[0].model_dump() == b.model_dump()
    assert events.sessions(TEAM, "p") == ["s2", "s1"]
    assert events.count(TEAM) == 4 and events.count("other") == 1


def test_the_store_redacts_every_text_field(events):
    code = "```python\ndef leak_marker_fn(p):\n    return os.system(p)\n```"
    events.append(Event(team_id=TEAM, project="p", session_id="s", domain="coding", source="x", kind="step",
                        text=f"Do it {code}", action=Action(name="bash", target=code),
                        result=Result(facts={"failing": [code], "nested": {"k": code}}), data={"note": code}))
    stored = json.dumps(events.session(TEAM, "p", "s")[0].model_dump(mode="json"))
    assert "leak_marker_fn" not in stored and "[code removed]" in stored and "Do it" in stored


# --- the coding adapter: hook payloads -> events -------------------------------------------------------

def test_every_hook_type_becomes_its_event(project_dir):
    src = str(Path(project_dir) / "src" / "shop.py")
    assert [e.data for e in hook_events(project_dir, "session-start", {"source": "compact"})] == [{"source": "compact"}]
    [prompt] = hook_events(project_dir, "user-prompt", {"prompt": "Add refunds. Never use floats for money."})
    assert prompt.kind == "prompt" and prompt.text.startswith("Add refunds")
    [edit] = hook_events(project_dir, "post-tool-use", {
        "tool_name": "Edit", "tool_input": {"file_path": src, "old_string": "a", "new_string": "b"},
        "tool_response": {"filePath": src, "success": True}})
    assert edit.action.model_dump() == {"name": "edit", "target": "src/shop.py", "risky": False, "risk": None}
    assert edit.result.ok is True
    [run] = hook_events(project_dir, "post-tool-use", {
        "tool_name": "Bash", "tool_input": {"command": "./ci.sh"},
        "tool_response": {"stdout": "FAILED tests/test_a.py::test_x - assert 1\n==== 1 failed, 3 passed in 0.1s ====",
                          "stderr": "", "interrupted": False}})
    assert run.action.name == "test_run" and run.result.ok is False
    assert run.result.metrics == {"passed": 3, "failed": 1, "total": 4}
    assert run.result.facts == {"runner": "pytest", "failing": ["tests/test_a.py::test_x"]}
    [cmd] = hook_events(project_dir, "post-tool-use", {"tool_name": "Bash", "tool_input": {"command": "ls -la"},
                                                        "tool_response": {"stdout": "x"}})
    assert (cmd.action.name, cmd.action.target, cmd.result.ok) == ("bash", "ls -la", None)
    [turn] = hook_events(project_dir, "stop", {"stop_hook_active": False}, usage={"tokens": 1200, "latency_ms": None})
    assert turn.kind == "turn_end" and turn.usage == {"tokens": 1200.0}
    assert [e.text for e in hook_events(project_dir, "pre-compact", {"trigger": "manual"})] == ["manual"]
    assert [e.text for e in hook_events(project_dir, "session-end", {"reason": "prompt_input_exit"})] \
        == ["prompt_input_exit"]


def test_irrelevant_and_malformed_payloads_make_no_events_and_never_raise(project_dir):
    assert hook_events(project_dir, "post-tool-use", {"tool_name": "Read", "tool_input": {"file_path": "a"}}) == []
    assert hook_events(project_dir, "user-prompt", {"prompt": "   "}) == []
    assert hook_events(project_dir, "stop", {"stop_hook_active": True}) == []  # the same turn, continued
    assert hook_events(project_dir, "pre-tool-use", {"tool_name": "Bash"}) == []
    for payload in ({}, {"tool_name": "Bash", "tool_input": None}, {"tool_name": "Edit", "tool_input": {}},
                    {"tool_name": "Bash", "tool_input": {"command": "pytest"}, "tool_response": 42},
                    {"tool_name": None, "tool_response": [1, 2]}):
        hook_events(project_dir, "post-tool-use", payload)  # no exception
    [mcp] = hook_events(project_dir, "post-tool-use", {"tool_name": "mcp__horizon__show_memories"})
    assert (mcp.action.name, mcp.action.target) == ("mcp", "mcp__horizon__show_memories")


def test_paths_are_project_relative_and_outside_files_keep_only_their_name(project_dir):
    assert relative_path(f"{project_dir}/pkg/a.py", project_dir) == "pkg/a.py"
    assert relative_path("pkg/b.py", project_dir) == "pkg/b.py"
    assert relative_path("/Users/someone/secrets/notes.txt", project_dir) == "…/notes.txt"
    assert relative_path(None, project_dir) is None


RISKY_COMMANDS = {
    "rm -rf build/": "destructive", "git reset --hard HEAD~1": "destructive", "git clean -fd": "destructive",
    "git checkout -- .": "destructive", "psql -c \"DROP TABLE users\"": "destructive",
    "git push --force origin main": "force_push", "git push origin main": "publish",
    "alembic upgrade head": "migration", "python manage.py migrate": "migration", "npx prisma migrate dev": "migration",
    "terraform apply -auto-approve": "deploy", "kubectl apply -f k8s/": "deploy", "npm publish": "publish",
    "twine upload dist/*": "publish", "pip install requests": "dependency", "uv add httpx": "dependency",
    "npm install lodash": "dependency", "yarn add react": "dependency", "pip install -U pip && pytest": "dependency",
}
SAFE_COMMANDS = ["pytest -q", "./ci.sh", "ls -la", "git status", "git diff", "git commit -m \"wip\"",
                 "pip install -r requirements.txt", "pip install -e .", "npm install", "npm install && npm test",
                 "uv sync", "rm tmp.txt", "rm -f out.log", "python -m shop.cli export", "cat pyproject.toml"]
RISKY_PATHS = {"requirements.txt": "dependency", "pyproject.toml": "dependency", "web/package.json": "dependency",
               "uv.lock": "dependency", "app/migrations/0002_add_refunds.py": "migration",
               "alembic/versions/abc_add.py": "migration", ".github/workflows/ci.yml": "ci_config",
               ".env.production": "secrets"}
SAFE_PATHS = ["shop/orders.py", "tests/test_orders.py", "README.md", "CHANGELOG.md", "docs/migrations.md"]


def test_the_risk_classifier_matches_the_labelled_set():
    assert {c: classify_command(c)[1] for c in RISKY_COMMANDS} == RISKY_COMMANDS
    assert [c for c in SAFE_COMMANDS if classify_command(c)[0]] == []
    assert {p: classify_path(p)[1] for p in RISKY_PATHS} == RISKY_PATHS
    assert [p for p in SAFE_PATHS if classify_path(p)[0]] == []
    adapter = get_adapter("coding")
    assert adapter.classify("bash", {"command": "alembic upgrade head"}) == (True, "migration")
    assert adapter.classify("write", {"path": "package.json"}) == (True, "dependency")
    with pytest.raises(ValueError):
        get_adapter("cooking")


def test_risky_actions_are_flagged_on_the_raw_command_but_stored_redacted(project_dir):
    [e] = hook_events(project_dir, "post-tool-use", {"tool_name": "Bash", "tool_input": {
        "command": "python -c \"import os\" && alembic upgrade head"}, "tool_response": {"stdout": ""}})
    assert (e.action.risky, e.action.risk, e.action.target) == (True, "migration", 'python -c "…" && alembic upgrade head')


# --- replay: events -> states and transitions -----------------------------------------------------------

def session(project):
    def post(tool, tool_input, response=None):
        return hook_events(project, "post-tool-use", {"tool_name": tool, "tool_input": tool_input,
                                                      "tool_response": response or {}})
    fail = {"stdout": "FAILED tests/test_o.py::test_refund - x\n==== 1 failed, 4 passed in 0.2s ===="}
    ok = {"stdout": "==== 5 passed in 0.2s ===="}
    return [*hook_events(project, "session-start", {"source": "startup"}),
            *hook_events(project, "user-prompt", {"prompt": "Ticket #7: add partial refunds."}),
            *post("Edit", {"file_path": f"{project}/shop/orders.py"}),
            *post("Bash", {"command": "pytest -q"}, fail),
            *post("Edit", {"file_path": f"{project}/shop/orders.py"}),
            *post("Write", {"file_path": f"{project}/CHANGELOG.md", "content": "x"}),
            *post("Bash", {"command": "pip install freezegun"}),
            *post("Bash", {"command": "pytest -q"}, ok),
            *hook_events(project, "stop", {}, usage={"tokens": 5000, "turns": 3}),
            *hook_events(project, "pre-compact", {"trigger": "auto"}),
            *hook_events(project, "session-end", {"reason": "other"})]


def test_replay_gives_the_state_at_every_step_and_one_transition_per_step(project_dir):
    events = session(project_dir)
    states, transitions = replay(events, CodingAdapter())
    assert len(states) == len(events) and len(transitions) == 6
    first_run = transitions[1]
    assert first_run.action.name == "test_run" and first_run.before["domain"]["tests"] is None
    assert first_run.after["domain"]["tests"] == {"passed": 4, "failed": 1, "total": 5,
                                                  "failing": ["tests/test_o.py::test_refund"]}
    final = states[-1]
    assert final["domain"] == {"edits": 3, "files": ["CHANGELOG.md", "shop/orders.py"], "commands": 1,
                               "test_runs": 2, "failed_test_runs": 1,
                               "tests": {"passed": 5, "failed": 0, "total": 5, "failing": []}}
    assert final["core"] == {"steps": 6, "prompts": 1, "turns": 1, "compactions": 1, "risky": 1, "tokens": 5000.0}
    assert transitions[4].action.risk == "dependency"
    assert transitions[2].before == transitions[1].after  # steps chain: each starts where the last ended


def test_replay_is_deterministic_and_pure(project_dir):
    events = session(project_dir)
    snapshot = [e.model_dump() for e in events]
    first, second = replay(events, CodingAdapter()), replay(events, CodingAdapter())
    assert first[0] == second[0] and [t.after for t in first[1]] == [t.after for t in second[1]]
    assert [e.model_dump() for e in events] == snapshot


def test_the_session_outcome_comes_from_its_events(project_dir):
    adapter = CodingAdapter()
    out = adapter.outcome(session(project_dir))
    assert out.success == 1.0 and out.signal == "auto"
    assert out.metrics == {"test_runs": 2, "failed_test_runs": 1, "first_try": 0.0, "edits": 3, "files": 2,
                           "commands": 1, "turns": 1, "tokens": 5000.0}
    no_tests = hook_events(project_dir, "user-prompt", {"prompt": "Explain the code."})
    assert adapter.outcome(no_tests).success is None and "first_try" not in adapter.outcome(no_tests).metrics
    assert adapter.outcome([]) is None
    assert adapter.situation(session(project_dir)) == "Ticket #7: add partial refunds."


# --- the hooks write the log -----------------------------------------------------------------------------

def transcript(tmp_path):
    rows = [
        {"type": "user", "message": {"role": "user", "content": "first task"}, "timestamp": "2026-09-30T10:00:00Z"},
        {"type": "assistant", "message": {"id": "m0", "usage": {"input_tokens": 999}}, "timestamp": "2026-09-30T10:00:05Z"},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "now add refunds"}]},
         "timestamp": "2026-09-30T10:01:00Z"},
        {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 100, "output_tokens": 20}},
         "timestamp": "2026-09-30T10:01:02Z"},
        {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 100, "output_tokens": 20}},
         "timestamp": "2026-09-30T10:01:03Z"},  # the same streamed turn, written twice
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
         "timestamp": "2026-09-30T10:01:04Z"},  # a tool result, not a new prompt
        {"type": "assistant", "isSidechain": True, "message": {"id": "sub", "usage": {"input_tokens": 5000}},
         "timestamp": "2026-09-30T10:01:05Z"},  # a subagent's turn
        {"type": "assistant", "message": {"id": "m2", "usage": {"input_tokens": 50, "output_tokens": 10,
                                                                "cache_read_input_tokens": 300}},
         "timestamp": "2026-09-30T10:01:10Z"},
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return str(path)


def test_turn_usage_counts_this_turn_once_without_subagents(tmp_path):
    usage = turn_usage(transcript(tmp_path))
    assert usage == {"tokens": 480, "input": 150, "output": 30, "cache_write": 0, "cache_read": 300, "turns": 2,
                     "latency_ms": 10000}
    assert turn_usage(None) is None and turn_usage(str(tmp_path / "missing.jsonl")) is None


def test_lean_hooks_append_the_session_to_the_log(settings, events, project_dir, tmp_path):
    def hook(name, **payload):
        return run_hook(name, json.dumps({"session_id": "s1", "cwd": project_dir, **payload}), settings, profile="lean")

    hook("session-start", source="startup")
    out = hook("user-prompt", prompt="Add refunds.")
    assert "Horizon task id" in out  # the lean handler still answers as before
    hook("post-tool-use", tool_name="Edit", tool_input={"file_path": f"{project_dir}/shop.py"}, tool_response={})
    hook("post-tool-use", tool_name="Bash", tool_input={"command": "pytest"},
         tool_response={"stdout": "==== 2 passed in 0.1s ===="})
    hook("stop", transcript_path=transcript(tmp_path), stop_hook_active=False)
    hook("pre-compact", trigger="auto")
    hook("session-end", reason="other")
    logged = events.session(settings.team_id, project_dir, "s1")
    assert [e.kind for e in logged] == ["session_start", "prompt", "step", "step", "turn_end", "compaction",
                                        "session_end"]
    assert logged[4].usage["tokens"] == 480
    assert CodingAdapter().outcome(logged).success == 1.0


def test_an_event_log_failure_never_changes_or_breaks_a_hook(settings, task_store, project_dir, monkeypatch):
    def boom(self, event):
        raise RuntimeError("disk full")

    monkeypatch.setattr(EventStore, "append", boom)
    out = run_hook("user-prompt", json.dumps({"session_id": "s1", "cwd": project_dir, "prompt": "Add refunds."}),
                   settings, profile="lean")
    assert "Horizon task id" in out and len(task_store.active(settings.team_id, cwd=project_dir)) == 1


def test_the_installer_adds_the_event_hooks_for_local_lean_only(tmp_path):
    def hooks(**kw):
        install(tmp_path, command=["py", "-m", "horizon"], **kw)
        return json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]

    lean = hooks()
    assert {"PreCompact", "SessionEnd", "UserPromptSubmit"} <= set(lean)
    assert lean["PostToolUse"][0]["matcher"] == "Bash|Edit|Write|MultiEdit|NotebookEdit"
    full = hooks(profile="full")
    assert not {"PreCompact", "SessionEnd", "UserPromptSubmit"} & set(full)
    assert full["PostToolUse"][0]["matcher"] == "Bash"
    hosted = hooks(hosted="https://h.example")
    assert "PreCompact" not in hosted and "SessionEnd" not in hosted and "UserPromptSubmit" in hosted
