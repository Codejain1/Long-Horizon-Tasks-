"""World model level 1: task prior, per-phase fits, calibration, a task-held-out gate and the doom-stop policy."""

import json
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "runner"))
import forecast as FC  # noqa: E402

KINDS = {"todo store with json file": 0.85, "csv report by category": 0.5, "house conventions store errors": 0.15}


def log(solved: bool, rng) -> list[dict]:
    """A synthetic run log: solved jobs make progress (checks pass, tests rise); doomed ones keep failing."""
    ev, t = [{"t": 0.0, "event": "plan", "steps": [{"id": f"s{i}"} for i in range(4)]}], 0.0
    for k in range(8):
        t += 45 + rng.random() * 30
        ok = solved and rng.random() < 0.8 or (not solved and rng.random() < 0.15)
        passed = (k + 1) if solved else 1
        ev.append({"t": t, "event": "check", "step": f"s{min(k, 3)}", "ok": ok,
                   "detail": f"{passed} passed, {max(0, 6 - passed)} failed"})
        if not ok and rng.random() < 0.5:
            ev.append({"t": t, "event": "decision", "option": "escalate"})
    ev.append({"t": t, "event": "report", "ok": solved})
    return ev


def jobs(n=150, seed=0):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        kind = rng.choice(list(KINDS))
        y = rng.random() < KINDS[kind]
        out.append((f"{kind} variant {i % 7}", FC.states(log(y, rng)), int(y)))
    return out


def test_states_use_rates_and_only_what_was_known_then():
    ev = [{"t": 0, "event": "plan", "steps": [{"id": "a"}, {"id": "b"}]},
          {"t": 60, "event": "check", "step": "a", "ok": True, "detail": "2 passed, 2 failed"},
          {"t": 120, "event": "decision", "option": "escalate"},
          {"t": 180, "event": "check", "step": "b", "ok": False, "detail": "3 passed, 1 failed"},
          {"t": 200, "event": "report"}, {"t": 300, "event": "check", "step": "c", "ok": True}]
    s = FC.states(ev)
    assert len(s) == 2 and s[0]["minutes"] == 1 and s[0]["done_frac"] == 0.5 and s[0]["fail_rate"] == 0.0
    assert s[1]["fail_rate"] == 0.5 and s[1]["escalations"] == 1 and s[1]["pass_trend"] == 0.25


def test_task_prior_follows_similar_past_jobs_and_never_its_own_task():
    goals = ["todo store with json file a", "todo store with json file b", "csv report by category a"]
    vec = FC.Tfidf(goals)
    V = vec(goals)
    ys = np.array([1.0, 1.0, 0.0])
    p_todo = FC.prior(vec(["todo store with json file c"])[0], V, ys, 0.5)
    p_csv = FC.prior(vec(["csv report by category z"])[0], V, ys, 0.5)
    assert p_todo > 0.6 > 0.5 > p_csv
    f = FC.Forecaster().fit(jobs(60))
    g = f.spec["goals"][0]
    assert f.task_prior(g) != f.task_prior(g + " extra")  # its own task's history is excluded


def test_replay_is_task_held_out_and_beats_the_base_rate_with_signal():
    r = FC.replay(jobs())
    assert r["passes"] and r["brier"] < r["brier_base"] and r["tasks"] <= 21
    assert r["phases"]["from 2 min"]["auc"] > 0.75
    noise = [(g, st, int(random.Random(i).random() < 0.5)) for i, (g, st, _) in enumerate(jobs())]
    assert not FC.replay(noise)["passes"]


def test_policy_saves_minutes_without_stopping_solved_jobs():
    r = FC.replay(jobs(200, seed=3))
    pol = FC.choose_policy(r["_per_job"], budget=10, max_false=0.02)
    solved = sum(j["y"] for j in r["_per_job"])
    assert pol["minutes_saved"] > 0 and pol["stopped_but_solved"] <= 0.02 * solved


def test_spec_round_trips_and_only_a_passed_gate_is_loaded(tmp_path):
    js = jobs(80)
    f = FC.Forecaster().fit(js)
    path = tmp_path / "f.json"
    path.write_text(json.dumps({**f.spec, "gate": {"passes": True}, "policy": {"after": 0.3, "below": 0.1}}))
    g = FC.load(path)
    assert np.allclose(g.predict(js[0][0], js[0][1]), f.predict(js[0][0], js[0][1]))
    path.write_text(json.dumps({**f.spec, "gate": {"passes": False}, "policy": {"after": 0.3, "below": 0.1}}))
    assert FC.load(path) is None
    path.write_text(json.dumps({**f.spec, "embed": True, "gate": {"passes": True}, "policy": {"after": 0.3, "below": 0.1}}))
    assert FC.load(path) is None  # trained on our encoder: never served with TF-IDF priors


def test_runner_stops_a_doomed_job_only_past_the_policy_share(tmp_path):
    import longrun as LR
    from test_longrun import FakeModel, call

    class Doomed:
        spec = {"policy": {"after": 0.4, "below": 0.1}}

        def task_prior(self, goal):
            return 0.1

        def predict(self, goal, st, pri=None):
            return np.array([0.02])
    plan = [{"id": "s1", "do": "x", "check": {"cmd": "false"}}]
    m = FakeModel([plan] * 5, [[call("done", summary="tried")]] * 40)
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", max_minutes=10, forecast=Doomed())
    rn.log.write_text(json.dumps({"t": time.time() - 300, "event": "plan", "steps": [{"id": "s1"}]}) + "\n")
    r = rn.run()
    assert not r["ok"] and r["reason"].startswith("forecast: P(solved) = 0.02")
    rn2 = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws2"), None, tmp_path / "r2.jsonl", max_minutes=10, forecast=Doomed())
    now = time.time()
    rn2.log.write_text(json.dumps({"t": now - 120, "event": "plan", "steps": [{"id": "s1"}]}) + "\n"
                       + json.dumps({"t": now, "event": "check", "step": "s1", "ok": False, "detail": ""}) + "\n")
    assert rn2._doomed() == ""  # 2 of 10 minutes: before the policy's share of the budget


def test_exploration_logs_the_rule_the_pick_and_its_probability(tmp_path):
    import longrun as LR
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), None, tmp_path / "r.jsonl", explore=1.0, seed=1)
    st = LR.Step("s", "x", {"cmd": "false"})
    hist = [{"option": "first try", "failed": 1, "passed": 0}]
    picks = {rn._explore("escalate", "failed", st, hist, 0, 2)[0] for _ in range(40)}
    assert picks == {"escalate", "continue", "replan"}  # every allowed action gets tried
    opt, why, info = rn._explore("escalate", "failed", st, hist, 0, 2)
    assert info["explored"] and info["rule_option"] == "escalate" and abs(info["p_pick"] - 1 / 3) < 1e-3
    assert rn._explore("stop", "out of re-plans", st, hist, 2, 2)[2] == {}  # stopping is never explored
    rn0 = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws0"), None, tmp_path / "r0.jsonl")
    assert rn0._explore("escalate", "failed", st, hist, 0, 2) == ("escalate", "failed", {})  # off by default
    rn5 = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws5"), None, tmp_path / "r5.jsonl", explore=0.2, seed=2)
    infos = [rn5._explore("escalate", "failed", st, hist, 0, 2)[2] for _ in range(500)]
    share = sum(i["explored"] for i in infos) / 500
    assert 0.14 < share < 0.26 and all(i["p_pick"] > 0 for i in infos)
