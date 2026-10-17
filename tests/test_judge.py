"""The judge: probabilities logged, labelled by real outcomes, calibrated; judge checks and a verdict per job."""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "runner"))
import judge as J  # noqa: E402
import longrun as LR  # noqa: E402
from test_longrun import FakeModel, call  # noqa: E402


def overconfident(messages, json_mode=False, **kw):  # always very sure, right only 60 % of the time (as labelled)
    return {"content": json.dumps({"p": 0.95, "reason": "looks done"})}


def test_isotonic_is_monotone_and_matches_observed_rates():
    f = J.isotonic([0.1, 0.1, 0.5, 0.5, 0.9, 0.9, 0.9, 0.9], [0, 0, 1, 0, 1, 1, 1, 0])
    assert f(0.1) == 0.0 and f(0.5) == 0.5 and f(0.9) == 0.75 and f(0.0) == 0.0 and f(1.0) == 0.75
    g = J.isotonic([0.2, 0.4, 0.6], [1, 0, 1])  # a violation is pooled: never decreasing
    assert g(0.2) <= g(0.4) <= g(0.6)


def test_answers_are_logged_and_calibrated_once_enough_labels(tmp_path):
    j = J.Judge(overconfident, tmp_path / "j.db", min_labels=20)
    first = j.ask("job_done", "done?", "evidence")
    assert first == {"id": 1, "p": 0.95, "raw": 0.95, "reason": "looks done", "calibrated": False}
    rng = random.Random(3)
    for _ in range(30):
        j.label(j.ask("job_done", "done?", "e")["id"], int(rng.random() < 0.6))
    later = j.ask("job_done", "done?", "e")
    assert later["calibrated"] and abs(later["p"] - 0.6) < 0.2 and later["raw"] == 0.95
    assert not j.ask("step", "other family?", "e")["calibrated"]  # each question family earns its own map


def test_report_shows_whether_calibration_helps(tmp_path):
    rng = random.Random(1)
    j = J.Judge(None, tmp_path / "j.db")
    for _ in range(200):  # raw scores informative but too extreme: calibration should cut the Brier score
        raw = rng.choice([0.05, 0.95])
        y = int(rng.random() < (0.3 if raw < 0.5 else 0.7))
        jid = j.db.execute("INSERT INTO judgments (ts, family, question, raw, reason) VALUES (0, 'f', 'q', ?, '')",
                           (raw,)).lastrowid
        j.label(jid, y)
    [r] = j.report()
    assert r["n"] == 200 and r["brier_calibrated"] < r["brier_raw"] and r["brier_calibrated"] < r["brier_base"]
    assert r["ece_calibrated"] < r["ece_raw"]


def test_a_broken_answer_is_dont_know(tmp_path):
    j = J.Judge(lambda m, **k: {"content": "no json here"}, tmp_path / "j.db")
    assert j.ask("step", "q", "e")["p"] == 0.5


def test_judge_check_and_the_jobs_verdict(tmp_path):
    plan = [{"id": "w", "kind": "build", "do": "write the trip summary", "check": {"judge": "Does summary.md list 3 options?"}}]
    m = FakeModel([plan], [[call("write_file", path="summary.md", content="1. A\n2. B\n3. C\n")],
                           [call("done", summary="summary written")]])
    asked = []

    def jm(messages, json_mode=False, **kw):
        asked.append(messages[0]["content"])
        return {"content": json.dumps({"p": 0.9 if "3 options" in messages[0]["content"] else 0.4, "reason": "r"})}
    rn = LR.Runner("plan a trip", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl",
                   judge=J.Judge(jm, tmp_path / "j.db"))
    r = rn.run()
    assert r["ok"] and "--- summary.md\n1. A" in asked[0]  # the judge saw the file the step wrote
    assert r["judge_id"] == 2 and r["judge_p"] == 0.4 and "acceptance test would pass" in asked[1]


def test_without_a_judge_a_judge_check_fails_plainly(tmp_path):
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), None, tmp_path / "r.jsonl")
    ok, why = rn.check(LR.Step("s", "x", {"judge": "good?"}))
    assert not ok and "no judge" in why


def test_benchmarks_label_the_verdict_with_the_hidden_result(tmp_path):
    sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
    import bench as B
    j = J.Judge(overconfident, tmp_path / "judge.db")
    jid = j.ask("job_done", "done?", "e")["id"]
    B.label_judge(tmp_path / "judge.db", jid, False)
    B.label_judge(tmp_path / "judge.db", None, True)  # a job without a verdict: nothing to label
    assert j.db.execute("SELECT outcome FROM judgments").fetchall() == [(0.0,)]


def test_parallel_asks_lose_nothing(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    j = J.Judge(overconfident, tmp_path / "j.db")
    with ThreadPoolExecutor(8) as ex:
        ids = list(ex.map(lambda k: j.ask("job_done", f"q{k}", "e")["id"], range(80)))
    assert len(set(ids)) == 80 and j.db.execute("SELECT COUNT(*) FROM judgments").fetchone()[0] == 80
