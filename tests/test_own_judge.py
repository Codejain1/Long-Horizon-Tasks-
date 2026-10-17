"""Our own judge: the decision layer's scorer with nothing outside, calibrated on real outcomes."""

import json

import pytest

from horizon.decision.scorers import OwnJudge, make_scorer, noul, score

Q = {"o1_success": noul("Will option 1 work?"), "o1_fit": score("How well does it fit?", ["poor", "ok", "great"])}


def judge(tmp_path, answers, **kw):
    seen = []

    def chat(prompt):
        seen.append(prompt)
        return "sure: " + json.dumps(answers)
    j = OwnJudge("", chat=chat, db_path=str(tmp_path / "judge.db"), **kw)
    return j, seen


def test_answers_both_question_shapes_and_logs_them(tmp_path):
    j, seen = judge(tmp_path, {"q0": 0.8, "q1": 1})
    out = j.ask({"task": "add a cache"}, Q)
    assert out["o1_success"].value == 0.8 and out["o1_fit"].value == 0.5  # level 1 of 0..2
    assert '"task": "add a cache"' in seen[0] and "q1 (level 0-2)" in seen[0]
    assert set(j.last_ids) == set(Q) and j.db.execute("SELECT COUNT(*) FROM judgments").fetchone()[0] == 2
    assert OwnJudge.family("o1_success") == "o_success"


def test_calibrates_on_real_outcomes(tmp_path):
    j, _ = judge(tmp_path, {"q0": 0.9, "q1": 2}, min_labels=10)
    for k in range(10):  # the judge says 0.9 but only 3 in 10 of those really worked
        j.ask({}, Q)
        j.label(j.last_ids["o1_success"], 1.0 if k < 3 else 0.0)
    assert abs(j.ask({}, Q)["o1_success"].value - 0.3) < 1e-9  # now 0.9 means what it really meant: 30 %
    assert j.ask({}, Q)["o1_fit"].value == 1.0  # levels are positions, not rates: left as they are


def test_selected_by_settings_and_needs_an_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("HORIZON_OWN_JUDGE_DB", str(tmp_path / "j.db"))
    assert isinstance(make_scorer("own", "jev-latest", "x", "http://127.0.0.1:8000/v1"), OwnJudge)
    with pytest.raises(ValueError):
        make_scorer("own", "jev-latest", "x", "")
    from horizon.config import Settings
    s = Settings.from_env({"HORIZON_SCORER": "own", "HORIZON_OWN_JUDGE_URL": "http://127.0.0.1:8000/v1"})
    assert s.scorer == "own" and s.own_judge_url == "http://127.0.0.1:8000/v1"


def test_isotonic_pools_equal_scores_and_stays_monotone():
    from horizon.decision.scorers import _isotonic
    f = _isotonic([0.2, 0.2, 0.5, 0.5, 0.9, 0.9, 0.9], [0, 1, 0, 1, 1, 1, 0])
    assert f(0.2) == 0.5 and f(0.5) == 0.5 and abs(f(0.9) - 2 / 3) < 1e-9 and f(0.95) == f(0.9)
