"""Phase 7: inspection tools, human approvals via MCP user input, accounts, credits and the account page."""

import json
import os
import subprocess
import sys

import anyio
import httpx2
import mcp.types as types
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from test_decision import CLOSE, FakeScorer, with_scorer
from test_mcp_server import payload

from horizon.accounts import TOOL_CREDITS, Accounts, OutOfCredits
from horizon.db import connect
from horizon.memrouter.embedding import HashEmbedder
from horizon.memrouter.router import MemRouter
from horizon.models import Actual, Predicted
from horizon.server import create_server, http_app
from horizon.service import Platform


@pytest.fixture
def anyio_backend():
    return "asyncio"


def user(*answers):
    """An MCP client's user: answers each user-input request in turn, and records the questions."""
    asked, queue = [], list(answers)

    async def callback(context, params):
        asked.append(params)
        action, content = queue.pop(0)
        return types.ElicitResult(action=action, content=content)

    return callback, asked


async def call(client, tool, **args):
    return payload(await client.call_tool(tool, args))


def platform_with_memory(settings, task_store, episode_store, project_dir, scorer=None):
    import dataclasses

    mr = MemRouter(episode_store, HashEmbedder(settings.embedding_dim), dataclasses.replace(settings, consolidation_every=0))
    return Platform(settings, task_store, lambda: mr, cwd=project_dir, scorer_factory=lambda: scorer), mr


# --- inspection tools -------------------------------------------------------------------

@pytest.mark.anyio
async def test_explain_decision(settings, task_store, memrouter, project_dir):
    platform = with_scorer(settings, task_store, memrouter, project_dir,
                           FakeScorer(**CLOSE, second={"success": (0.9, 0.3)}))
    async with Client(create_server(platform)) as client:
        task_id = (await call(client, "start_task", goal="Add persistence"))["task_id"]
        r = await call(client, "evaluate_options", task_id=task_id, situation="choose a database",
                       options=[{"label": "PostgreSQL"}, {"label": "MongoDB"}])
        await call(client, "submit_consequences", task_id=task_id, decision_id=r["decision_id"], results=[
            {"option": "PostgreSQL", "spike": {"ran": True, "passed": True}},
            {"option": "MongoDB", "spike": {"ran": True, "passed": False}}])
        why = await call(client, "explain_decision", task_id=task_id)  # the latest decision by default
        assert why["decision_id"] == r["decision_id"] and why["final"]["chosen"] == "PostgreSQL"
        assert [p["decision"] for p in why["passes"]] == ["close", "clear_winner"]
        assert {o["label"]: o["eliminated"] for o in why["options"]} == {"PostgreSQL": False, "MongoDB": True}
        assert why["evidence"]["consequence_mode"] == "spikes" and len(why["evidence"]["checks"]) == 2
        bad = await client.call_tool("explain_decision", {"task_id": task_id, "decision_id": "dec_nope"})
        assert bad.is_error


@pytest.mark.anyio
async def test_show_memories(settings, task_store, episode_store, project_dir):
    platform, mr = platform_with_memory(settings, task_store, episode_store, project_dir)
    for _ in range(3):
        mr.record(team_id="local", situation="choose a formatter", chosen="black", actual=Actual(success=1.0),
                  predicted=Predicted(success=0.7))
    mr.record(team_id="local", situation="migrate the users table", chosen="drop and recreate",
              actual=Actual(success=0.0), predicted=Predicted(success=0.8), severity="severe")
    mr.consolidate("local")
    async with Client(create_server(platform)) as client:
        everything = (await call(client, "show_memories"))["memories"]
        kinds = {m["kind"] for m in everything}
        assert {"strategy", "fear", "episode"} <= kinds
        strategy = next(m for m in everything if m["kind"] == "strategy")
        assert strategy["evidence"] == 3 and "stability" in strategy and strategy["provenance"]["evidence"]
        fears = (await call(client, "show_memories", kinds=["fear"]))["memories"]
        assert len(fears) == 1 and fears[0]["severity"] == "severe"
        found = (await call(client, "show_memories", query="choose a code formatter"))["memories"]
        assert found[0]["chosen"] == "black"


@pytest.mark.anyio
async def test_delete_memory_asks_the_user_and_is_traceable(settings, task_store, episode_store, project_dir):
    platform, mr = platform_with_memory(settings, task_store, episode_store, project_dir)
    bad = mr.record(team_id="local", situation="choose a date library", chosen="moment.js", actual=Actual(success=1.0),
                    predicted=Predicted(success=0.5)).episode
    callback, asked = user(("decline", None), ("accept", {"confirm": True}))
    async with Client(create_server(platform), elicitation_callback=callback) as client:
        kept = await call(client, "delete_memory", memory_id=bad.id, reason="moment.js is deprecated")
        assert kept["removed"] is False
        gone = await call(client, "delete_memory", memory_id=bad.id, reason="moment.js is deprecated")
    assert gone == {"memory_id": bad.id, "kind": "episode", "action": "archived", "links_removed": 0,
                    "removed": True}
    assert "moment.js is deprecated" in asked[0].message
    assert episode_store.get(bad.id, "local") is not None  # archived, never deleted
    assert bad.id not in [m.episode_id for m in mr.recall(team_id="local", situation="choose a date library").memories]
    assert mr.graph.removals("local")[0]["removed_by"] == "user"
    assert [a["action"] for a in task_store.approvals("local")] == ["decline", "accept"]


@pytest.mark.anyio
async def test_fear_lessons_are_cleared_only_by_a_named_human(settings, task_store, episode_store, project_dir):
    platform, mr = platform_with_memory(settings, task_store, episode_store, project_dir)
    mr.record(team_id="local", situation="rotate the keys", chosen="in place", actual=Actual(success=0.0),
              predicted=Predicted(success=0.8), severity="severe")
    [fear] = mr.graph.lessons("local", fear_only=True)

    async with Client(create_server(platform)) as client:  # a client that can't ask the user
        refused = await client.call_tool("clear_fear", {"lesson_id": fear.id})
        assert refused.is_error and "horizon clear-fear" in refused.content[0].text
        deleting = await client.call_tool("delete_memory", {"memory_id": fear.id, "reason": "x"})
        assert deleting.is_error and "clear_fear" in deleting.content[0].text

    callback, asked = user(("cancel", None), ("accept", {"confirm": True, "your_name": "Kartik"}))
    async with Client(create_server(platform), elicitation_callback=callback) as client:
        assert (await call(client, "clear_fear", lesson_id=fear.id))["cleared"] is False
        done = await call(client, "clear_fear", lesson_id=fear.id)
    assert done["cleared"] and done["cleared_by"] == "Kartik"
    assert mr.graph.get_lesson(fear.id, "local").cleared_by == "Kartik"
    assert task_store.approvals("local")[-1]["approver"] == "Kartik"


# --- human approvals for high-stakes actions --------------------------------------------------

def high_stakes(settings, task_store, memrouter, project_dir):
    return with_scorer(settings, task_store, memrouter, project_dir, FakeScorer(**CLOSE, high_stakes=0.9))


async def reach_ask_human(client):
    task_id = (await call(client, "start_task", goal="Migrate users table"))["task_id"]
    r = await call(client, "evaluate_options", task_id=task_id, situation="migrate the production users table",
                   options=[{"label": "drop and recreate"}, {"label": "expand/contract"}])
    assert r["decision"] == "check_consequences"
    r = await call(client, "submit_consequences", task_id=task_id, decision_id=r["decision_id"], results=[])
    return task_id, r


@pytest.mark.anyio
async def test_a_high_stakes_tie_is_decided_by_the_user(settings, task_store, memrouter, project_dir):
    platform = high_stakes(settings, task_store, memrouter, project_dir)
    callback, asked = user(("accept", {"option": "expand/contract", "your_name": "Kartik"}))
    async with Client(create_server(platform), elicitation_callback=callback) as client:
        task_id, r = await reach_ask_human(client)
        assert r["decision"] == "human_choice" and r["chosen"] == "expand/contract"
        schema = asked[0].requested_schema["properties"]["option"]
        assert set(schema["enum"]) == {"drop and recreate", "expand/contract"}
        # The choice is the decision: logged for the world model and credited to the human.
        record = platform.decisions.get(r["decision_id"], "local")
        assert record["final"]["decision"] == "human_choice" and record["final"]["approver"] == "Kartik"
        out = await call(client, "record_outcome", task_id=task_id, situation="migrate", chosen="expand/contract",
                         success=1.0)
        assert memrouter.store.get(out["episode_id"], "local").predicted.source == "jev"
    assert task_store.approvals("local")[0]["answer"] == "expand/contract"


@pytest.mark.anyio
async def test_without_user_input_the_host_is_told_to_ask(settings, task_store, memrouter, project_dir):
    platform = high_stakes(settings, task_store, memrouter, project_dir)
    async with Client(create_server(platform)) as client:  # no elicitation support
        _, r = await reach_ask_human(client)
    assert r["decision"] == "ask_human" and r["chosen"] is None
    assert task_store.approvals("local")[0]["action"] == "unsupported"

    callback, _ = user(("decline", None))
    async with Client(create_server(platform), elicitation_callback=callback) as client:
        _, r = await reach_ask_human(client)
    assert r["decision"] == "ask_human" and "Ask them in chat" in r["next"]


@pytest.mark.anyio
async def test_an_escalation_asks_the_user_for_guidance(settings, task_store, memrouter, project_dir):
    platform = Platform(settings, task_store, lambda: memrouter, cwd=project_dir)
    callback, asked = user(("accept", {"guidance": "Use the stdlib csv module instead"}))
    async with Client(create_server(platform), elicitation_callback=callback) as client:
        task_id = (await call(client, "start_task", goal="Add CSV export"))["task_id"]
        for chosen in ("pandas", "polars"):
            await call(client, "record_outcome", task_id=task_id, situation="s", chosen=chosen, success=0.0)
        out = await call(client, "record_outcome", task_id=task_id, situation="s", chosen="xlsx", success=0.0)
    assert out["rollback"]["action"] == "escalate" and out["human_guidance"].startswith("Use the stdlib")
    assert out["task_status"] == "active" and "pandas" in asked[0].message
    assert "Human guidance: Use the stdlib csv module" in platform.tasks.get(task_id, "local").progress[-1].note


# --- accounts and credits -----------------------------------------------------------------

def test_keys_are_hashed_verified_and_revocable(settings):
    db = connect(settings.db_url)
    accounts = Accounts(db)
    team, key = accounts.create_team("Acme")
    assert key.startswith("hzn_") and accounts.verify_key(key) == team
    assert key not in json.dumps(db.fetchall("SELECT * FROM api_keys"), default=str)  # only a hash is stored
    assert accounts.balance(team) == 1000  # free starter credits
    assert accounts.verify_key("hzn_wrong") is None and accounts.verify_key(None) is None
    [k] = accounts.keys(team)
    assert accounts.revoke_key(team, k["id"]) and accounts.verify_key(key) is None
    assert not accounts.revoke_key("team_other", k["id"])


def test_credits_are_charged_and_enforced(settings):
    accounts = Accounts(connect(settings.db_url))
    team, _ = accounts.create_team("Acme", credits=6)
    accounts.check(team, "evaluate_options")
    accounts.charge(team, "evaluate_options", session_id="s1", task_id="t1",
                    signals={"decision": "clear_winner", "eliminated": 1})
    assert accounts.balance(team) == 1
    with pytest.raises(OutOfCredits):
        accounts.check(team, "submit_consequences")
    accounts.check(team, "show_memories")  # inspection is free
    accounts.charge(team, "record_outcome", session_id="s1", task_id="t1", signals={"rollback": "rollback"})
    [s] = accounts.savings(team)
    assert (s["session"], s["decisions_scored"], s["options_eliminated"], s["regressions_caught"], s["credits"]) == \
        ("s1", 1, 1, 1, TOOL_CREDITS["evaluate_options"] + TOOL_CREDITS["record_outcome"])


# --- the hosted server: metered MCP and the account page -------------------------------------

@pytest.fixture
def hosted(settings):
    import dataclasses

    app = http_app(dataclasses.replace(settings, dev_api_key="dev-key"))
    accounts = Accounts(connect(settings.db_url))
    team, key = accounts.create_team("Acme")
    return app, accounts, team, key


@pytest.mark.anyio
async def test_hosted_mcp_is_metered_per_team(hosted, settings, unused_tcp_port):
    import uvicorn
    from mcp.client.streamable_http import streamable_http_client

    app, accounts, team, key = hosted
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=unused_tcp_port, log_level="warning"))
    url = f"http://127.0.0.1:{unused_tcp_port}/mcp"
    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)
        with anyio.fail_after(30):
            while not server.started:
                await anyio.sleep(0.05)
            async with httpx2.AsyncClient() as raw:
                assert (await raw.post(url, json={})).status_code == 401
                assert (await raw.post(url, json={}, headers={"Authorization": "Bearer hzn_nope"})).status_code == 401
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"})
            async with Client(streamable_http_client(url, http_client=http)) as client:
                task_id = (await call(client, "start_task", goal="team task"))["task_id"]
                await call(client, "recall_context", task_id=task_id, situation="s")
                await call(client, "show_memories")
            await http.aclose()
        server.should_exit = True
    assert accounts.balance(team) == 1000 - TOOL_CREDITS["start_task"] - TOOL_CREDITS["recall_context"]
    # 2026-07-28 streamable HTTP is stateless (no mcp-session-id), so a Horizon task stands in for the session.
    session = next(s for s in accounts.savings(team) if s["tasks"] == 1)
    assert session["calls"] == 2 and session["session"] == f"task:{task_id}"
    # Team isolation: the task lives in the team's scope, not the local one.
    from horizon.taskstate.store import TaskStore

    tasks = TaskStore(connect(settings.db_url))
    assert tasks.get(task_id, team) is not None and tasks.get(task_id, "local") is None


def test_account_page(hosted):
    app, accounts, team, key = hosted
    accounts.charge(team, "evaluate_options", session_id="sess-1", task_id="t1", signals={"decision": "clear_winner"})
    web = TestClient(app, base_url="https://testserver")
    assert "Sign in" in web.get("/").text
    assert web.post("/login", data={"api_key": "hzn_nope"}).status_code == 401
    r = web.post("/login", data={"api_key": key}, follow_redirects=False)
    assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower() and \
        "samesite=strict" in r.headers["set-cookie"].lower()
    page = web.get("/account").text
    assert "Acme" in page and "995" in page and "sess-1" in page and "Savings per session" in page
    created = web.post("/keys", data={"label": "ci"}).text
    new_key = created.split("<code class=key>")[1].split("</code>")[0]
    assert accounts.verify_key(new_key) == team
    [first, second] = accounts.keys(team)
    web.post(f"/keys/{second['id']}/revoke")
    assert accounts.verify_key(new_key) is None
    web.post("/logout")
    assert "Sign in" in web.get("/account").text  # redirected to sign in


def test_account_json_api(hosted):
    app, accounts, team, key = hosted
    api = TestClient(app)
    assert api.get("/api/account").status_code == 401
    me = api.get("/api/account", headers={"Authorization": f"Bearer {key}"}).json()
    assert me["team"]["id"] == team and me["credits"] == 1000 and me["prices"]["evaluate_options"] == 5
    made = api.post("/api/keys?label=bot", headers={"Authorization": f"Bearer {key}"}).json()
    assert accounts.verify_key(made["key"]) == team
    assert api.delete(f"/api/keys/{made['id']}", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert api.delete(f"/api/keys/{made['id']}", headers={"Authorization": f"Bearer {key}"}).status_code == 404


def test_team_cli(settings):
    env = {**os.environ, "HORIZON_DB_URL": settings.db_url}
    out = subprocess.run([sys.executable, "-m", "horizon", "create-team", "Acme", "--credits", "50"],
                         capture_output=True, text=True, env=env, timeout=60).stdout
    team = out.split()[1]
    key = out.strip().split()[-1]
    accounts = Accounts(connect(settings.db_url))
    assert accounts.verify_key(key) == team and accounts.balance(team) == 50
    subprocess.run([sys.executable, "-m", "horizon", "add-credits", team, "25"], env=env, timeout=60, check=True,
                   capture_output=True)
    assert accounts.balance(team) == 75


@pytest.fixture
def unused_tcp_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.anyio
async def test_approvals_on_legacy_sessions_ask_mid_call(settings, task_store, memrouter, episode_store, project_dir):
    """Protocol <= 2025-11-25: the server sends elicitation/create mid-call (no InputRequiredResult round trip)."""
    platform = high_stakes(settings, task_store, memrouter, project_dir)
    callback, asked = user(("accept", {"option": "drop and recreate"}))
    async with Client(create_server(platform), elicitation_callback=callback, mode="legacy") as client:
        _, r = await reach_ask_human(client)
    assert r["decision"] == "human_choice" and r["chosen"] == "drop and recreate" and len(asked) == 1

    plat2, mr = platform_with_memory(settings, task_store, episode_store, project_dir)
    mr.record(team_id="local", situation="wipe the cache cluster", chosen="flushall", actual=Actual(success=0.0),
              predicted=Predicted(success=0.8), severity="severe")
    [fear] = mr.graph.lessons("local", fear_only=True)
    callback, _ = user(("accept", {"confirm": True, "your_name": "Kartik"}))
    async with Client(create_server(plat2), elicitation_callback=callback, mode="legacy") as client:
        assert (await call(client, "clear_fear", lesson_id=fear.id))["cleared_by"] == "Kartik"


def test_request_state_cannot_be_forged():
    from horizon.approvals import sign, verify

    token = sign({"tool": "clear_fear", "args": {"lesson_id": "les_1"}})
    body, mac = token.rsplit(".", 1)
    import base64

    forged = base64.urlsafe_b64encode(b'{"tool":"clear_fear","args":{"lesson_id":"les_2"}}').decode() + "." + mac
    assert verify(token)["args"]["lesson_id"] == "les_1" and verify(forged) is None and verify("junk") is None
