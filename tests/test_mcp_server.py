"""The three tools over real MCP: in-process, stdio subprocess and streamable HTTP."""

import json
import os
import subprocess
import sys

import anyio
import httpx2
import pytest
from mcp import Client, StdioServerParameters

from horizon.server import APIKeyMiddleware, create_server

TOOLS = {"start_task", "recall_context", "evaluate_options", "submit_consequences", "record_outcome"}


def payload(result) -> dict:
    assert not result.is_error, result
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


async def run_flow(client: Client) -> dict:
    listed = await client.list_tools()
    names = {t.name for t in listed.tools}
    assert TOOLS <= names
    for tool in listed.tools:
        assert "Call this" in tool.description  # directive descriptions

    started = payload(await client.call_tool("start_task", {"goal": "Add CSV export", "plan": ["write", "test"]}))
    task_id = started["task_id"]
    ctx = payload(await client.call_tool("recall_context", {
        "task_id": task_id, "situation": "choose a csv writer",
        "conditions": [{"key": "lang", "op": "=", "value": "python"}],
    }))
    assert ctx["task_state"]["goal"] == "Add CSV export"
    out = payload(await client.call_tool("record_outcome", {
        "task_id": task_id, "situation": "choose a csv writer", "chosen": "stdlib csv",
        "tests_passed": 4, "tests_failed": 0, "predicted_success": 0.9, "recall_id": ctx["recall_id"],
        "task_complete": True,
    }))
    assert out["episode_id"].startswith("ep_")
    assert out["surprise"] == pytest.approx(0.1)
    assert out["task_status"] == "completed"

    again = payload(await client.call_tool("start_task", {"goal": "Add TSV export"}))
    ctx2 = payload(await client.call_tool("recall_context", {"task_id": again["task_id"],
                                                             "situation": "choose a csv writer"}))
    assert ctx2["memories"][0]["chosen"] == "stdlib csv"

    bad = await client.call_tool("recall_context", {"task_id": "task_nope", "situation": "x"})
    assert bad.is_error and "start_task" in bad.content[0].text
    return out


@pytest.mark.anyio
async def test_in_process(platform):
    async with Client(create_server(platform)) as client:
        assert "start_task" in (client.instructions or "")
        await run_flow(client)


@pytest.mark.anyio
async def test_stdio_subprocess(settings, project_dir):
    env = {**os.environ, "HORIZON_DB_URL": settings.db_url, "HORIZON_EMBEDDER": "hash",
           "HORIZON_PROJECT_DIR": project_dir}
    params = StdioServerParameters(command=sys.executable, args=["-m", "horizon", "serve"], env=env)
    with anyio.fail_after(60):
        async with Client(params) as client:
            await run_flow(client)


@pytest.mark.anyio
async def test_http_requires_api_key(platform):
    app = APIKeyMiddleware(create_server(platform).streamable_http_app(), "dev-key")
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
        missing = await http.post("/mcp", json={})
        wrong = await http.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert missing.status_code == 401 and wrong.status_code == 401


@pytest.mark.anyio
async def test_http_with_api_key(platform, unused_tcp_port):
    import uvicorn

    app = APIKeyMiddleware(create_server(platform).streamable_http_app(), "dev-key")
    config = uvicorn.Config(app, host="127.0.0.1", port=unused_tcp_port, log_level="warning")
    server = uvicorn.Server(config)
    url = f"http://127.0.0.1:{unused_tcp_port}/mcp"

    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)
        with anyio.fail_after(30):
            while not server.started:
                await anyio.sleep(0.05)
            http = httpx2.AsyncClient(headers={"Authorization": "Bearer dev-key"})
            from mcp.client.streamable_http import streamable_http_client

            async with Client(streamable_http_client(url, http_client=http)) as client:
                await run_flow(client)
            await http.aclose()
        server.should_exit = True


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def unused_tcp_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_hook_cli(name: str, body: dict, env: dict) -> dict | None:
    proc = subprocess.run([sys.executable, "-m", "horizon", "hook", name], input=json.dumps(body),
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


@pytest.mark.anyio
async def test_simulated_claude_code_session(settings, project_dir):
    """Hooks and the stdio server run as separate processes sharing one store, as under Claude Code."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env.update(HORIZON_DB_URL=settings.db_url, HORIZON_EMBEDDER="hash", HORIZON_PROJECT_DIR=project_dir)
    params = StdioServerParameters(command=sys.executable, args=["-m", "horizon", "serve"], env=env)

    start = run_hook_cli("session-start", {"cwd": project_dir, "source": "startup"}, env)
    assert "start_task" in start["hookSpecificOutput"]["additionalContext"]

    with anyio.fail_after(60):
        async with Client(params) as client:
            task_id = payload(await client.call_tool("start_task", {"goal": "Fix the date bug"}))["task_id"]
            subprocess.run(["git", "-C", project_dir, "init", "-q"], check=True)
            subprocess.run(["git", "-C", project_dir, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q",
                            "--allow-empty", "-m", "init"], check=True)
            # Claude Code runs the PreToolUse hook before each recall_context: it snapshots the project.
            run_hook_cli("pre-tool-use", {"cwd": project_dir, "session_id": "s1",
                                          "tool_name": "mcp__horizon__recall_context"}, env)
            ctx = payload(await client.call_tool("recall_context", {"task_id": task_id,
                                                                    "situation": "fix timezone parsing"}))
            # The model runs the tests; the PostToolUse hook captures the real counts.
            run_hook_cli("post-tool-use", {
                "cwd": project_dir, "session_id": "s1", "tool_name": "Bash",
                "tool_input": {"command": "python -m pytest -q"},
                "tool_response": {"stdout": "==== 2 failed, 6 passed in 0.3s ====", "stderr": ""},
            }, env)
            # The model tries to stop without recording: the Stop hook blocks once.
            blocked = run_hook_cli("stop", {"cwd": project_dir, "session_id": "s1", "stop_hook_active": False},
                                   env)
            assert blocked["decision"] == "block"
            # The model self-reports all green; the captured counts win.
            out = payload(await client.call_tool("record_outcome", {
                "task_id": task_id, "situation": "fix timezone parsing", "chosen": "use zoneinfo",
                "tests_passed": 8, "tests_failed": 0, "recall_id": ctx["recall_id"],
                "failure_reason": "DST edge case still wrong",
            }))
            assert out["test_results_source"] == "hook" and out["actual_success"] == 0.75
            # 2 tests failed, so the host is told to roll back to the snapshot and retry.
            assert out["rollback"]["action"] == "rollback"
            assert out["rollback"]["restore"]["checkpoint_id"] == ctx["checkpoint_id"] is not None
            assert out["rollback"]["restore"]["git"].startswith("git -C ")
            assert run_hook_cli("stop", {"cwd": project_dir, "stop_hook_active": False}, env) is None

    resumed = run_hook_cli("session-start", {"cwd": project_dir, "source": "resume"}, env)
    assert task_id in resumed["hookSpecificOutput"]["additionalContext"]


@pytest.mark.anyio
async def test_record_outcome_sets_provenance_from_client_info(platform, memrouter):
    async with Client(create_server(platform)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert "ctx" not in tools["record_outcome"].input_schema["properties"]
        task_id = payload(await client.call_tool("start_task", {"goal": "g"}))["task_id"]
        out = payload(await client.call_tool("record_outcome", {
            "task_id": task_id, "situation": "s", "chosen": "c", "success": 1.0, "plan": ["step 2"]}))
    agent = memrouter.store.get(out["episode_id"], "local").provenance.agent_id
    assert agent and "/" in agent  # "<client name>/<version>"
    assert platform.tasks.get(task_id, "local").plan == ["step 2"]


@pytest.mark.anyio
async def test_evaluate_options_over_mcp(platform):
    async with Client(create_server(platform)) as client:
        task_id = payload(await client.call_tool("start_task", {"goal": "Add persistence"}))["task_id"]
        out = payload(await client.call_tool("evaluate_options", {
            "task_id": task_id, "situation": "choose a database",
            "options": [{"label": "PostgreSQL", "est_cost_usd": 20}, {"label": "SQLite", "est_cost_usd": 0.5}],
        }))
    # No scorer configured by default: ranked on the host's estimates, the host decides.
    assert out["decision"] == "unscored" and out["chosen"] is None and out["scorer_status"] == "not_configured"
    assert out["cheapest_by_estimates"] == ["SQLite", "PostgreSQL"]


@pytest.mark.anyio
async def test_consequence_round_trip_over_mcp(settings, task_store, memrouter, project_dir):
    from test_decision import CLOSE, FakeScorer, with_scorer

    platform = with_scorer(settings, task_store, memrouter, project_dir,
                           FakeScorer(**CLOSE, second={"success": (0.9, 0.3)}))
    async with Client(create_server(platform)) as client:
        task_id = payload(await client.call_tool("start_task", {"goal": "Add persistence"}))["task_id"]
        r = payload(await client.call_tool("evaluate_options", {
            "task_id": task_id, "situation": "choose a database",
            "options": [{"label": "PostgreSQL"}, {"label": "MongoDB"}]}))
        assert r["decision"] == "check_consequences"
        final = payload(await client.call_tool("submit_consequences", {
            "task_id": task_id, "decision_id": r["decision_id"],
            "results": [{"option": "PostgreSQL", "static_checks": [{"name": "dry run", "passed": True}],
                         "spike": {"ran": True, "passed": True, "tests_passed": 3, "metrics": {"latency_ms": 4}}},
                        {"option": "MongoDB", "spike": {"ran": False}}]}))
        assert final["decision"] == "clear_winner" and final["chosen"] == "PostgreSQL"
        bad = await client.call_tool("submit_consequences", {"task_id": task_id, "decision_id": r["decision_id"],
                                                             "results": []})
        assert bad.is_error and "already made" in bad.content[0].text
