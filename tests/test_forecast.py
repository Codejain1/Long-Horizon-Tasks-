"""World model v0: states from run logs, a replay gate against the base rate, and what a doom stop would do."""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "runner"))
import forecast as FC  # noqa: E402


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


def test_states_only_use_what_was_known_then():
    ev = [{"t": 0, "event": "plan", "steps": [{"id": "a"}, {"id": "b"}]},
          {"t": 60, "event": "check", "step": "a", "ok": True, "detail": "2 passed, 2 failed"},
          {"t": 120, "event": "decision", "option": "escalate"},
          {"t": 180, "event": "check", "step": "b", "ok": False, "detail": "3 passed, 1 failed"},
          {"t": 200, "event": "report"}, {"t": 300, "event": "check", "step": "c", "ok": True}]
    s = FC.states(ev)
    assert len(s) == 2 and s[0]["minutes"] == 1 and s[0]["done_frac"] == 0.5 and s[0]["pass_rate"] == 0.5
    assert s[1]["failed_checks"] == 1 and s[1]["escalations"] == 1 and s[1]["pass_trend"] == 0.25


def test_replay_gate_beats_the_base_rate_only_when_there_is_signal():
    rng = random.Random(0)
    jobs = [(FC.states(log(y, rng)), int(y)) for y in [rng.random() < 0.45 for _ in range(120)]]
    r = FC.replay(jobs)
    assert r["passes"] and r["brier_model"] < r["brier_base"]
    p = FC.doom_policy(r["_per_job"], budget_min=10)
    assert p["stopped"] > 0 and p["stopped_but_solved"] <= p["stopped"] * 0.2
    noise = [(st, int(rng.random() < 0.45)) for st, _ in jobs]  # labels unrelated to the logs
    assert not FC.replay(noise)["passes"]


def test_model_round_trips_through_json():
    rng = random.Random(1)
    jobs = [(FC.states(log(y, rng)), int(y)) for y in [rng.random() < 0.5 for _ in range(40)]]
    import numpy as np
    X = FC.matrix([s for st, _ in jobs for s in st])
    m = FC.Model().fit(X, np.array([y for st, y in jobs for _ in st], float))
    m2 = FC.Model.from_json(json.loads(json.dumps(m.to_json())))
    assert abs(float(m.predict(X[:5]).sum()) - float(m2.predict(X[:5]).sum())) < 1e-9


def test_runner_stops_a_doomed_job_only_past_the_budget_share(tmp_path):
    import longrun as LR
    from test_longrun import FakeModel, call

    class Doomed:
        def predict(self, X):
            return __import__("numpy").array([0.02])
    plan = [{"id": "s1", "do": "x", "check": {"cmd": "false"}}]
    m = FakeModel([plan] * 5, [[call("done", summary="tried")]] * 40)
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", max_minutes=10, forecast=Doomed())
    assert rn._doomed() == ""  # nothing logged yet
    import time
    rn.log.write_text(json.dumps({"t": time.time() - 300, "event": "plan", "steps": [{"id": "s1"}]}) + "\n")
    r = rn.run()
    assert not r["ok"] and r["reason"].startswith("forecast: P(solved) = 0.02")
    rn2 = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws2"), None, tmp_path / "r2.jsonl", max_minutes=10, forecast=Doomed())
    now = time.time()
    rn2.log.write_text(json.dumps({"t": now - 120, "event": "plan", "steps": [{"id": "s1"}]}) + "\n"
                       + json.dumps({"t": now, "event": "check", "step": "s1", "ok": False, "detail": ""}) + "\n")
    assert rn2._doomed() == ""  # 2 of 10 minutes: under 40 % of the budget, never stopped by the forecast
    rn2.forecast = None
    rn2.log.write_text(json.dumps({"t": now - 500, "event": "plan", "steps": []}) + "\n"
                       + json.dumps({"t": now, "event": "check", "step": "s1", "ok": False}) + "\n")
    assert rn2._doomed() == ""  # no model that passed its gate: no forecast stop at all
