"""End-to-end: the whole flow the way a host drives it, against the hosted server over real HTTP.

A team is created; a Claude Code-shaped host (real hook subprocesses plus an MCP client with a team key) works
through a task in a real git repo:
- start_task, then the baseline test run (hook), with one pre-existing failure;
- recall_context (the hook snapshots git);
- a crucial, high-stakes close call: consequence plan → spikes → submit_consequences → a second tie → the user
  picks the option through an MCP user-input request;
- an attempt that regresses → rollback → restore (git) → recall with the failure fed back → a passing attempt;
- the next similar task: memory recalls what worked;
- explain_decision, show_memories, the sleep job, the decision export, the account page's credits and savings.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import httpx2
import mcp.types as types
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from test_mcp_server import payload

from horizon.accounts import TOOL_CREDITS, Accounts
from horizon.db import connect
from horizon.decision.log import SCHEMA

PRE_EXISTING = "tests/test_other.py::test_unfinished"
REGRESSION = "tests/test_util.py::test_helpers"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


async def run_hook(name: str, body: dict, env: dict, remote: str) -> dict | None:
    """A hook as the host runs it: a subprocess, here in hosted mode (it calls the server's hook API)."""
    proc = await anyio.run_process([sys.executable, "-m", "horizon", "hook", name, "--remote", remote],
                                   input=json.dumps(body).encode(), env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.decode().strip()
    return json.loads(out) if out else None


def pytest_output(failing: list[str], passed: int) -> str:
    lines = [f"FAILED {t} - AssertionError" for t in failing]
    lines.append(f"==== {len(failing)} failed, {passed} passed in 0.2s ====" if failing
                 else f"==== {passed} passed in 0.2s ====")
    return "\n".join(lines)


class Host:
    """What Claude Code does around the model's tool calls: run hooks, answer user-input requests."""

    def __init__(self, repo: Path, env: dict, remote: str):
        self.repo, self.env, self.remote, self.session = repo, env, remote, "sess-e2e"

    async def hook(self, name: str, **body):
        return await run_hook(name, {"session_id": self.session, "cwd": str(self.repo), **body}, self.env, self.remote)

    async def run_tests(self, failing: list[str], passed: int):
        await self.hook("post-tool-use", tool_name="Bash", tool_input={"command": "python -m pytest -q"},
                        tool_response={"stdout": pytest_output(failing, passed), "stderr": ""})

    async def before_recall(self):
        return await self.hook("pre-tool-use", tool_name="mcp__horizon__recall_context", tool_input={})


@pytest.mark.anyio
async def test_full_flow(settings, tmp_path, unused_tcp_port, monkeypatch):
    import dataclasses

    import uvicorn

    from horizon.server import http_app
    from test_decision import CLOSE, FakeScorer

    # --- a hosted server with a scorer that makes the storage decision a high-stakes near tie ------------
    settings = dataclasses.replace(settings, consolidation_every=0)
    scorer = FakeScorer(**CLOSE, high_stakes=0.9)
    import horizon.server as server_module

    monkeypatch.setattr(server_module, "make_scorer", lambda *a: scorer)  # built lazily, on first use
    app = http_app(settings)
    accounts = Accounts(connect(settings.db_url))
    team, key = accounts.create_team("Acme")

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "app.py").write_text("v1\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "init")
    url = f"http://127.0.0.1:{unused_tcp_port}"
    # The user's machine: no database access, only the team key (hosted mode).
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HORIZON_", "CLAUDE_PROJECT_DIR"))}
    env["HORIZON_API_KEY"] = key
    host = Host(repo, env, url)
    offline = {**env, "HORIZON_DB_URL": settings.db_url, "HORIZON_EMBEDDER": "hash", "HORIZON_TEAM_ID": team,
               "HORIZON_EXPORT_DIR": settings.export_dir}  # the operator's side, for the sleep job and export
    answers = []

    async def user(context, params):  # the human, answering in the client
        answers.append(params.message)
        return types.ElicitResult(action="accept", content={"option": "SQLite", "your_name": "Kartik"})

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=unused_tcp_port, log_level="warning"))
    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)
        with anyio.fail_after(60):
            while not server.started:
                await anyio.sleep(0.05)
            from horizon.hooks import project_key

            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}",
                                               "X-Horizon-Project": project_key(str(repo))})
            from mcp.client.streamable_http import streamable_http_client

            async with Client(streamable_http_client(f"http://127.0.0.1:{unused_tcp_port}/mcp", http_client=http),
                              elicitation_callback=user) as mcp:
                async def call(tool, **args):
                    return payload(await mcp.call_tool(tool, args))

                start = await host.hook("session-start", source="startup")
                assert "start_task" in start["hookSpecificOutput"]["additionalContext"]

                # 1. Start, baseline, recall.
                task_id = (await call("start_task", goal="Persist the order cache so tests/test_cache.py passes",
                                      project_id="shop"))["task_id"]
                await host.run_tests([PRE_EXISTING, "tests/test_cache.py::test_persists"], passed=10)  # baseline
                await host.before_recall()
                ctx = await call("recall_context", task_id=task_id, situation="choose the cache storage")
                assert ctx["task_state"]["baseline"]["status"] == "captured" and ctx["checkpoint_id"]

                # 2. A high-stakes near tie: consequences, then the user decides.
                r = await call("evaluate_options", task_id=task_id, situation="choose the cache storage",
                               options=[{"label": "SQLite"}, {"label": "Redis"}])
                assert r["decision"] == "check_consequences" and len(r["consequence_plan"]["spikes"]) == 2
                r = await call("submit_consequences", task_id=task_id, decision_id=r["decision_id"], results=[
                    {"option": "SQLite", "spike": {"ran": True, "passed": True, "metrics": {"latency_ms": 2}}},
                    {"option": "Redis", "spike": {"ran": True, "passed": True, "metrics": {"latency_ms": 1}}}])
                assert r["decision"] == "human_choice" and r["chosen"] == "SQLite" and "Which option" in answers[0]
                decision_id = r["decision_id"]

                # 3. First attempt breaks an unrelated test: rollback, restore, feed the failure back.
                (repo / "app.py").write_text("broken\n")
                await host.run_tests([PRE_EXISTING, REGRESSION], passed=10)
                out = await call("record_outcome", task_id=task_id, situation="choose the cache storage",
                                 chosen="SQLite", recall_id=ctx["recall_id"],
                                 failure_reason="the shared helper changed behaviour")
                assert out["rollback"]["action"] == "rollback"
                assert out["test_judgement"]["regressions"] == [REGRESSION]
                assert out["test_judgement"]["pre_existing_failures"] == [PRE_EXISTING]
                # Hosted mode names no path ("git -C ."): the host runs it in the project directory.
                assert out["rollback"]["restore"]["git"].startswith("git -C . restore")
                subprocess.run(out["rollback"]["restore"]["git"], shell=True, check=True, cwd=repo)
                assert (repo / "app.py").read_text() == "v1\n"
                assert await host.before_recall() is None  # restored, so the recall isn't blocked
                retry = await call("recall_context", task_id=task_id, situation="choose the cache storage")
                assert retry["task_state"]["retry"]["previous_failures"][0]["reason"] == \
                    "the shared helper changed behaviour"

                # 4. Second attempt works (the pre-existing failure doesn't count).
                (repo / "app.py").write_text("v2\n")
                await host.run_tests([PRE_EXISTING], passed=12)
                done = await call("record_outcome", task_id=task_id, situation="choose the cache storage",
                                  chosen="SQLite", recall_id=retry["recall_id"], task_complete=True)
                assert done["actual_success"] == 1.0 and done["rollback"] is None and done["task_status"] == "completed"
                assert await host.hook("stop", stop_hook_active=False) is None  # nothing left unrecorded

                # 5. Inspection.
                why = await call("explain_decision", task_id=task_id, decision_id=decision_id)
                assert why["final"]["decision"] == "human_choice" and why["final"]["approver"] == "Kartik"
                assert [o["option"] for o in why["outcomes"]] == ["SQLite", "SQLite"]

                # 6. The next, similar task: memory brings back what worked.
                nxt = (await call("start_task", goal="Persist the session cache", project_id="shop"))["task_id"]
                await host.before_recall()
                later = await call("recall_context", task_id=nxt, situation="choose the cache storage")
                assert any(m["chosen"] == "SQLite" and m["success"] == 1.0 for m in later["memories"])
                memories = (await call("show_memories", query="cache storage"))["memories"]
                assert memories and all("SQLite" in m["chosen"] or m["kind"] != "fear" for m in memories)
            await http.aclose()
        server.should_exit = True

    # 7. Offline: the sleep job, the world-model export, and the account page.
    subprocess.run([sys.executable, "-m", "horizon", "consolidate"], env=offline, check=True, capture_output=True,
                   timeout=60)
    export = tmp_path / "decisions.jsonl"
    subprocess.run([sys.executable, "-m", "horizon", "export-decisions", "--out", str(export),
                    "--with-outcomes-only"], env=offline, check=True, capture_output=True, timeout=60)
    [record] = [json.loads(line) for line in export.read_text().splitlines()]
    assert record["schema"] == SCHEMA and record["team_id"] == team and len(record["outcomes"]) == 2
    assert "import" not in json.dumps(record)  # decision data, no code

    web = TestClient(app, base_url="https://testserver")
    web.post("/login", data={"api_key": key})
    page = web.get("/account").text
    spent = sum(TOOL_CREDITS[t] for t in ("start_task", "recall_context", "evaluate_options", "submit_consequences",
                                          "record_outcome")) + TOOL_CREDITS["start_task"] + 2 * TOOL_CREDITS[
        "recall_context"] + TOOL_CREDITS["record_outcome"]
    assert accounts.balance(team) == 1000 - spent and f"{1000 - spent:,}" in page
    [session] = [s for s in accounts.savings(team) if s["session"] == f"task:{task_id}"]
    assert session["regressions_caught"] == 1 and session["human_approvals"] == 1 and session["decisions_scored"] == 1


@pytest.fixture
def unused_tcp_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
