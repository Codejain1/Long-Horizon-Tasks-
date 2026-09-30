"""The lean profile: task continuity only (goals, constraints, team rules), one outcome per task."""

import json
from pathlib import Path

import pytest

from horizon.hooks import LEAN_WORKFLOW, WORKFLOW, run_hook
from horizon.install import install


def test_install_lean_writes_the_lean_snippet_env_and_hooks(tmp_path):
    install(tmp_path, command=["py", "-m", "horizon"])  # lean is the default
    assert "You don't need to call it" in (tmp_path / "CLAUDE.md").read_text()
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["horizon"]["env"] == {"HORIZON_PROFILE": "lean"}
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    assert "UserPromptSubmit" in hooks
    assert all(g["hooks"][0]["command"].endswith("--profile lean") for groups in hooks.values() for g in groups)
    install(tmp_path, command=["py", "-m", "horizon"], profile="full")  # back to full: replaces, doesn't duplicate
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    assert "UserPromptSubmit" not in hooks and [len(groups) for groups in hooks.values()] == [1, 1, 1, 1]
    assert "--profile" not in json.dumps(hooks) and "env" not in (tmp_path / ".mcp.json").read_text()


def test_install_hosted_lean_is_hooks_only(tmp_path):
    """Hosted lean: hooks that talk to the server, and no MCP entry (an earlier full install's is removed)."""
    install(tmp_path, command=["py", "-m", "horizon"], hosted="https://h.example", profile="full")
    assert "horizon" in json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    install(tmp_path, command=["py", "-m", "horizon"], hosted="https://h.example", profile="lean")
    assert "horizon" not in json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    commands = [g["hooks"][0]["command"] for groups in hooks.values() for g in groups]
    assert len(commands) == 5 and all("--remote https://h.example --profile lean" in c for c in commands)


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
    ctx = lean("user-prompt", {"cwd": project_dir, "session_id": "s1", "prompt": TEAM_PROMPT}, settings)
    assert lean("user-prompt", {"cwd": project_dir, "session_id": "s1", "prompt": "Also add a due date."},
                settings) is None  # the task id is given once
    [task] = task_store.active(settings.team_id, cwd=project_dir)
    assert f"Horizon task id: {task.id}" in ctx
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


def test_lean_server_offers_only_the_optional_tools(platform):
    import asyncio

    from horizon.server import create_server

    names = lambda server: {t.name for t in asyncio.run(server.list_tools())}  # noqa: E731
    full, lean_tools = names(create_server(platform, lean=False)), names(create_server(platform, lean=True))
    assert {"start_task", "recall_context", "record_outcome"} <= full
    assert full - lean_tools == {"start_task", "recall_context", "record_outcome"}


def test_hosted_lean_prompt_is_redacted_before_it_leaves_the_machine(monkeypatch):
    from horizon.hooks import Remote

    sent = []
    monkeypatch.setattr(Remote, "_post", lambda self, op, body: sent.append((op, body)))
    Remote("https://h.example", "hzn_x").user_prompt("prj_a", "s1", TEAM_PROMPT)
    [(op, body)] = sent
    assert op == "user-prompt" and "SECRET" not in json.dumps(body) and "uuid4().hex" in body["prompt"]


def test_team_rules_reach_a_teammate_on_another_machine(settings, free_tcp_port, monkeypatch):
    """Two people on one team, different machines and projects, one hosted server: Alice states the team's
    rules once; Bob's next session in his own project is shown them. Another team never is."""
    import dataclasses
    import threading
    import time as clock

    import uvicorn

    from horizon.accounts import Accounts
    from horizon.db import connect
    from horizon.server import http_app

    app = http_app(dataclasses.replace(settings))
    accounts = Accounts(connect(settings.db_url))
    team, alice_key = accounts.create_team("Acme")
    bob_key = accounts.create_key(team, "bob")[1]
    other_key = accounts.create_team("Other")[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=free_tcp_port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        clock.sleep(0.05)
    url = f"http://127.0.0.1:{free_tcp_port}"

    def hook(name, key, cwd, session, **body):
        monkeypatch.setenv("HORIZON_API_KEY", key)
        out = run_hook(name, json.dumps({"cwd": cwd, "session_id": session, **body}), remote=url, profile="lean")
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    try:
        hook("session-start", alice_key, "/alice/invoices", "a1")
        assert hook("user-prompt", alice_key, "/alice/invoices", "a1", prompt=TEAM_PROMPT) == ""  # no MCP tools
        bob = hook("session-start", bob_key, "/bob/payouts", "b1")
        assert "Team rules" in bob and "uuid4().hex" in bob and "SECRET" not in bob
        assert "uuid4" not in hook("session-start", other_key, "/bob/payouts", "o1")
    finally:
        server.should_exit = True


class RuleScorer:
    """A stand-in for Jev: says yes to the questions about the sentences listed."""

    def __init__(self, team=(), project=(), fail=False):
        self.team, self.project, self.fail, self.calls = set(team), set(project), fail, 0

    def ask(self, state, questions):
        from horizon.decision.scorers import Answer

        self.calls += 1
        if self.fail:
            raise RuntimeError("scorer down")
        out = {}
        for qid, q in questions.items():
            kind, i = qid.rsplit("_", 1)
            assert f"sentences[{i}]" in q["instructions"]
            hit = state["sentences"][int(i)] in (self.team if kind == "team" else self.project)
            out[qid] = Answer(0.9 if hit else 0.1)
        return out


def test_rules_are_picked_by_the_scorer_in_one_request():
    from horizon.hooks import split_rules

    text = "Always remember I prefer short commit messages, everywhere. Never mind that. Write tests."
    scorer = RuleScorer(team={"Always remember I prefer short commit messages, everywhere."})
    assert split_rules(text, scorer) == (["Always remember I prefer short commit messages, everywhere."], [])
    assert scorer.calls == 1
    # Keywords alone get both wrong: a team rule filed as a project rule, and "Never mind" as a rule.
    assert split_rules(text) == ([], ["Always remember I prefer short commit messages, everywhere.", "Never mind that."])


def test_a_failing_scorer_falls_back_to_keywords(settings, task_store, project_dir):
    from horizon.hooks import lean_user_prompt, split_rules

    assert split_rules(TEAM_PROMPT, RuleScorer(fail=True))[0][0].startswith("Our team's rules")
    lean_user_prompt(task_store, settings.team_id, project_dir, "s1", TEAM_PROMPT, scorer=RuleScorer(fail=True))
    assert task_store.team_rules(settings.team_id)[0].startswith("Our team's rules")
