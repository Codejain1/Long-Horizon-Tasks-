"""The world model (PROJECT.md §6): interface, the kernel model, the replay gate, wiring into the decision layer,
and the real attempt usage it learns efficiency from."""

import dataclasses
import json
import time
from datetime import UTC, datetime

import numpy as np
import pytest
from test_decision import CLOSE, OPTS, FakeScorer, with_scorer

from horizon.decision.worldmodel import (
    Forecast,
    KernelWorldModel,
    _Index,
    evaluate,
    gate,
    kernel_forecast,
    make_world_model,
    replay,
)
from horizon.hooks import run_hook
from horizon.models import Actual, Predicted
from horizon.taskstate.usage import attempt_usage

SITUATION = "choose a database for the order service"
T = "local"


def seed(mr, option, success, n, tokens=None, predicted=None, situation=SITUATION):
    return [mr.record(team_id=T, situation=situation, chosen=option,
                      actual=Actual(success=success, tokens=tokens),
                      predicted=Predicted(success=predicted, source="host") if predicted is not None else None).episode
            for _ in range(n)]


class ConstantModel:
    """A plugged-in world model (HORIZON_WORLD_MODEL_CLASS): sure of everything."""

    name = "constant"

    def __init__(self, router, settings):
        pass

    def predict(self, team_id, situation, conditions, options):
        return {o: Forecast(success=0.9, tokens=100.0, confidence=1.0, samples=50.0) for o in options}


# --- the model -----------------------------------------------------------------------------------------

def test_kernel_forecast_weights_by_similarity_and_shrinks_to_the_base_rate():
    f = kernel_forecast(np.array([1.0, 1.0, 0.3]), np.array([1.0, 1.0, 0.0]),
                        {"tokens": np.array([100.0, np.nan, 5.0])}, prior=0.5, min_similarity=0.5)
    assert f.success == pytest.approx((2 + 0.5) / 3)  # the 0.3-similar failure doesn't count
    assert f.samples == pytest.approx(2.0) and f.tokens == pytest.approx(100.0)
    empty = kernel_forecast(np.array([]), np.array([]), {"tokens": np.array([])}, prior=0.4, min_similarity=0.5)
    assert (empty.success, empty.confidence, empty.tokens) == (0.4, 0.0, None)


def test_forecasts_are_per_option(memrouter, settings):
    seed(memrouter, "PostgreSQL", 1.0, 4, tokens=5000)
    seed(memrouter, "MongoDB", 0.0, 4)
    f = KernelWorldModel(memrouter, settings).predict(T, SITUATION, None, ["PostgreSQL", "mongodb", "Cassandra"])
    assert f["PostgreSQL"].success > 0.75 and f["PostgreSQL"].tokens == pytest.approx(5000)
    assert f["mongodb"].success < 0.25 and f["mongodb"].tokens is None  # labels match like same_option
    assert f["Cassandra"].success == pytest.approx(0.5) and f["Cassandra"].confidence == 0  # the base rate
    assert f["PostgreSQL"].confidence == pytest.approx(4 / 7)


def test_the_index_learns_incrementally_keeps_archived_episodes_and_drops_purged_ones(memrouter, settings):
    wm = KernelWorldModel(memrouter, settings)
    eps = seed(memrouter, "PostgreSQL", 1.0, 3)
    idx = wm.index(T)
    seed(memrouter, "PostgreSQL", 0.0, 1)
    assert wm.index(T) is idx and len(idx.ids) == 4  # appended, not reloaded
    memrouter.graph.archive_episode(eps[0].id)  # out of retrieval, still training data (§7)
    assert eps[0].id in wm.index(T).ids
    memrouter.purge(T, eps[1].id, "legal request", "operator")  # erasure must reach the model too
    after = wm.index(T)
    assert after is not idx and eps[1].id not in after.ids and len(after.ids) == 3


def test_make_world_model_plugs_in_another_class(memrouter, settings):
    assert make_world_model(settings, memrouter).name == "kernel-v1"
    plugged = make_world_model(dataclasses.replace(settings, world_model_class="test_world_model:ConstantModel"),
                               memrouter)
    assert plugged.name == "constant"
    assert evaluate(plugged, T, settings)["passed"] is False  # replay is for the kernel model; plugged runs "on"


# --- the gate ------------------------------------------------------------------------------------------

def test_replay_beats_an_uninformed_stand_in_and_the_gate_passes(memrouter, settings):
    for _ in range(20):
        seed(memrouter, "PostgreSQL", 1.0, 1, predicted=0.5)
        seed(memrouter, "MongoDB", 0.0, 1, predicted=0.5)
    wm = KernelWorldModel(memrouter, settings)
    report = replay(wm.index(T), settings.world_model_min_similarity, 0.5)
    host = report["versus"]["host"]
    assert host["pairs"] >= 30 and host["model_brier"] < host["stand_in_brier"] == 0.25
    assert report["brier"] < report["brier_option_blind"]  # knowing the option is what helps
    assert gate(report, 30)["passed"]
    assert not gate(report, 1000)["passed"] and "not enough" in gate(report, 1000)["reason"]


def test_the_gate_fails_against_a_better_stand_in(memrouter, settings):
    for _ in range(20):
        seed(memrouter, "PostgreSQL", 1.0, 1, predicted=1.0)  # a perfect host
        seed(memrouter, "MongoDB", 0.0, 1, predicted=0.0)
    report = replay(KernelWorldModel(memrouter, settings).index(T), 0.5, 0.5)
    result = gate(report, 30)
    assert not result["passed"] and result["reason"] == "doesn't beat host"


def test_a_lead_that_luck_could_explain_doesnt_pass():
    report = {"versus": {"jev": {"pairs": 30, "model_brier": 0.24, "stand_in_brier": 0.25, "clearly_better": False}}}
    assert not gate(report, 30)["passed"]


def test_replay_is_fast_enough_for_auto_mode():
    rng = np.random.default_rng(0)
    idx = _Index(384)
    n = 2000
    v = rng.normal(size=(n, 384)).astype(np.float32)
    idx.vectors = v / np.linalg.norm(v, axis=1, keepdims=True)
    idx.labels = [f"opt{i % 5}" for i in range(n)]
    idx.ids = {str(i) for i in range(n)}
    idx.success = rng.random(n)
    idx.targets = {t: rng.random(n) for t in ("tokens", "cost_usd", "latency_ms")}
    idx.predicted = [("host", 0.5, False)] * n
    started = time.monotonic()
    replay(idx, 0.0, 0.5)
    assert time.monotonic() - started < 5


# --- wiring into evaluate_options ------------------------------------------------------------------------

def platform_with(settings, task_store, memrouter, project_dir, scorer=None, **kw):
    return with_scorer(dataclasses.replace(settings, **kw), task_store, memrouter, project_dir,
                       scorer or FakeScorer(**CLOSE, second={"success": (0.9, 0.3)}))


def test_shadow_mode_logs_forecasts_without_changing_the_decision(settings, task_store, memrouter, project_dir):
    seed(memrouter, "PostgreSQL", 1.0, 6, tokens=5000)
    seed(memrouter, "MongoDB", 0.0, 6, tokens=9000)
    platform = platform_with(settings, task_store, memrouter, project_dir, world_model="shadow")
    task = platform.start_task("Add order persistence")["task_id"]
    r = platform.evaluate_options(task, SITUATION, OPTS)
    assert r["decision"] == "check_consequences"  # spikes, exactly as without a world model
    world = platform.decisions.get(r["decision_id"], T)["world_model"]
    assert world["active"] is False and world["mode"] == "shadow" and world["ms"] < 1000
    assert world["forecasts"]["PostgreSQL"]["success"] > 0.8
    assert world["forecasts"]["MongoDB"]["tokens"] == pytest.approx(9000)


def test_an_active_world_model_settles_a_close_call_without_spikes(settings, task_store, memrouter, project_dir):
    seed(memrouter, "PostgreSQL", 1.0, 6, tokens=5000)
    seed(memrouter, "MongoDB", 0.0, 6, tokens=9000)
    scorer = FakeScorer(**CLOSE, second={"success": (0.9, 0.3)})
    platform = platform_with(settings, task_store, memrouter, project_dir, scorer, world_model="on")
    task = platform.start_task("Add order persistence")["task_id"]
    r = platform.evaluate_options(task, SITUATION, OPTS)
    assert r["settled_by"] == "world_model" and r["decision"] == "clear_winner" and r["chosen"] == "PostgreSQL"
    consequences = scorer.calls[-1][0]["consequences"]  # Jev re-scored with the forecasts as evidence (§5)
    assert {c["option"] for c in consequences} == {"PostgreSQL", "MongoDB"} and "world_model" in consequences[0]
    rec = platform.decisions.get(r["decision_id"], T)
    assert rec["consequences"]["plan"]["mode"] == "world_model" and rec["stage"] == "decided"
    # The host gave no token estimates, so the forecasts stood in for them.
    assert rec["world_model"]["filled_estimates"] == ["tokens"]
    assert [o["est_tokens"] for o in rec["options"]] == pytest.approx([5000, 9000])


def test_thin_evidence_still_gets_spikes(settings, task_store, memrouter, project_dir):
    seed(memrouter, "PostgreSQL", 1.0, 1)
    seed(memrouter, "MongoDB", 0.0, 1)
    platform = platform_with(settings, task_store, memrouter, project_dir, world_model="on")
    r = platform.evaluate_options(platform.start_task("x")["task_id"], SITUATION, OPTS)
    assert r["decision"] == "check_consequences" and r["consequence_plan"]["mode"] == "spikes"


def test_auto_mode_waits_for_the_gate(settings, task_store, memrouter, project_dir):
    seed(memrouter, "PostgreSQL", 1.0, 6)
    seed(memrouter, "MongoDB", 0.0, 6)
    platform = platform_with(settings, task_store, memrouter, project_dir, world_model="auto")
    r = platform.evaluate_options(platform.start_task("x")["task_id"], SITUATION, OPTS)
    assert r["decision"] == "check_consequences"  # no paired outcomes yet: stays in shadow
    assert "not enough" in platform.world_model_gate()["reason"]

    for _ in range(20):
        seed(memrouter, "PostgreSQL", 1.0, 1, predicted=0.5)
        seed(memrouter, "MongoDB", 0.0, 1, predicted=0.5)
    assert platform.world_model_gate(refresh=True)["passed"]
    r = platform.evaluate_options(platform.start_task("y")["task_id"], SITUATION, OPTS)
    assert r["settled_by"] == "world_model"


def test_a_plugged_in_model_runs_when_switched_on(settings, task_store, memrouter, project_dir):
    platform = platform_with(settings, task_store, memrouter, project_dir, world_model="on",
                             world_model_class="test_world_model:ConstantModel")
    r = platform.evaluate_options(platform.start_task("x")["task_id"], SITUATION, OPTS)
    assert r["settled_by"] == "world_model"


def test_a_broken_world_model_never_blocks_a_decision(settings, task_store, memrouter, project_dir):
    platform = platform_with(settings, task_store, memrouter, project_dir, world_model="on",
                             world_model_class="no_such_module:Model")
    r = platform.evaluate_options(platform.start_task("x")["task_id"], SITUATION, OPTS)
    assert r["decision"] == "check_consequences"
    assert platform.decisions.get(r["decision_id"], T)["world_model"]["status"] == "unavailable"


def test_world_model_accuracy_is_tracked_as_a_predictor(settings, task_store, memrouter, project_dir):
    seed(memrouter, "PostgreSQL", 1.0, 3)
    platform = platform_with(settings, task_store, memrouter, project_dir, world_model="shadow")
    task = platform.start_task("x")["task_id"]
    platform.evaluate_options(task, SITUATION, OPTS)
    platform.record_outcome(task, SITUATION, "PostgreSQL", success=1.0)
    [trust] = memrouter.predictor_trust(T, "world_model")
    assert trust["samples"] == 1 and trust["accuracy"] > 0.7


# --- real attempt usage from the transcript --------------------------------------------------------------

def transcript(path, with_recall=True):
    def assistant(mid, at, usage, tools=(), sidechain=False):
        return {"type": "assistant", "isSidechain": sidechain, "timestamp": at,
                "message": {"id": mid, "usage": usage,
                            "content": [{"type": "tool_use", "name": t, "input": {}} for t in tools]}}

    u = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}
    lines = [{"type": "user", "message": {"content": "add persistence"}},
             assistant("m0", "2026-09-29T10:00:00Z", {"input_tokens": 999},
                       ["mcp__horizon__recall_context"] if with_recall else []),
             assistant("m1", "2026-09-29T10:00:30Z", u),
             assistant("m1", "2026-09-29T10:00:31Z", u, ["Edit"]),  # the same streamed turn: counted once
             assistant("m2", "2026-09-29T10:00:40Z", {"input_tokens": 50000}, sidechain=True),  # a subagent
             assistant("m3", "2026-09-29T10:01:30Z", {"input_tokens": 1, "output_tokens": 2},
                       ["mcp__horizon__record_outcome"])]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return str(path)


def test_attempt_usage_sums_the_turns_since_the_last_recall(tmp_path):
    usage = attempt_usage(transcript(tmp_path / "t.jsonl"))
    assert usage == {"tokens": 138, "input": 11, "output": 7, "cache_write": 20, "cache_read": 100, "turns": 2,
                     "latency_ms": 90000}
    assert attempt_usage(transcript(tmp_path / "n.jsonl", with_recall=False)) is None
    assert attempt_usage(str(tmp_path / "missing.jsonl")) is None


def test_the_hook_captures_usage_and_record_outcome_uses_it(settings, platform, memrouter, project_dir, tmp_path,
                                                            monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    task = platform.start_task("x")["task_id"]
    hook = {"session_id": "s1", "cwd": project_dir, "tool_name": "mcp__horizon__record_outcome",
            "transcript_path": transcript(tmp_path / "t.jsonl")}
    assert run_hook("pre-tool-use", json.dumps(hook), settings) == ""  # silent
    out = platform.record_outcome(task, "s", "sqlite3", success=1.0)
    ep = memrouter.store.get(out["episode_id"], T)
    assert out["usage_source"] == "hook" and (ep.actual.tokens, ep.actual.latency_ms) == (138, 90000)

    run_hook("pre-tool-use", json.dumps(hook), settings)
    out = platform.record_outcome(task, "s", "sqlite3", success=1.0, tokens=5)  # the host's own count wins
    ep = memrouter.store.get(out["episode_id"], T)
    assert (ep.actual.tokens, ep.actual.latency_ms) == (5, 90000)
    assert platform.tasks.take_usage(project_dir, datetime(2000, 1, 1, tzinfo=UTC), "t") is None  # all consumed


def test_hosted_usage_is_scoped_to_the_team_and_sanitised(settings):
    from fastapi.testclient import TestClient

    from horizon.accounts import Accounts
    from horizon.db import connect
    from horizon.server import hosted_scope, http_app
    from horizon.taskstate.store import TaskStore

    api = TestClient(http_app(dataclasses.replace(settings)))
    team, key = Accounts(connect(settings.db_url)).create_team("A")
    body = {"project": "prj_x", "session_id": "s", "usage": {"tokens": "138", "latency_ms": 900, "junk": "x" * 99}}
    assert api.post("/api/hooks/usage", json=body, headers={"Authorization": f"Bearer {key}"}).status_code == 200
    got = TaskStore(connect(settings.db_url)).take_usage(hosted_scope(team, "prj_x"),
                                                        datetime(2000, 1, 1, tzinfo=UTC), "t")
    assert got["tokens"] == 138 and got["latency_ms"] == 900 and "junk" not in got
