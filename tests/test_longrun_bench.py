"""The fast benchmark: tasks are sound, orders are paired, the report pairs and bootstraps correctly."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
import bench as B  # noqa: E402
import tasks as T  # noqa: E402


def test_every_hidden_test_is_proven_on_its_reference_and_fails_empty():
    assert T.self_check() == []
    ids = [t.id for t in T.all_tasks()]
    assert len(ids) == len(set(ids)) == 29


def test_same_seed_same_order_different_seed_different():
    assert [t.id for t in B.order(1, 10)] == [t.id for t in B.order(1, 10)]
    assert [t.id for t in B.order(1, 10)] != [t.id for t in B.order(2, 10)]


def test_report_pairs_by_seed_and_position(tmp_path):
    rows = []
    for arm, solved in (("mem", [1, 1, 1, 0]), ("none", [1, 0, 0, 0])):
        for pos, s in enumerate(solved):
            rows.append({"arm": arm, "seed": 1, "pos": pos, "task": f"t{pos}", "family": "x", "passed": s, "total": 1,
                         "solved": bool(s), "minutes": 1.0 + pos, "interventions": 1 - s, "runner_ok": bool(s)})
    for arm in ("mem", "none"):
        (tmp_path / f"{arm}-1.jsonl").write_text("\n".join(json.dumps(r) for r in rows if r["arm"] == arm))
    out = B.report(str(tmp_path))
    assert "mem   jobs   4  solved  75.0%  (early half 100.0% -> late half  50.0%)" in out
    assert "none  jobs   4  solved  25.0%" in out and "paired on 4 jobs: +50.0 pts" in out


OUT = """FAILED test_hidden_acceptance.py::test_add_and_list - AssertionError: assert ['1. first'] == ['1. first item']
E       AssertionError: assert ['1. first'] == ['1. first item']
FAILED test_hidden_acceptance.py::test_count - AssertionError
1 failed, 2 passed"""


def test_feedback_reads_like_a_user_report():
    f = B.feedback(OUT)
    assert "test_add_and_list" in f and "['1. first'] == ['1. first item']" in f and "test_count" in f


def test_true_outcome_reaches_memory(tmp_path):
    import threading
    import time
    sys.path[:0] = [str(Path(__file__).parent.parent / "memrouter"), str(Path(__file__).parent)]
    import memory_service as M
    from test_hermes_memory import KeywordRanker, fake_encode
    mem = M.Memory(tmp_path, fake_encode, KeywordRanker())
    threading.Thread(target=M.serve, args=(mem, 18810), daemon=True).start()
    time.sleep(0.3)
    task = T.all_tasks()[0]
    _, wid = mem.fails.warn("basic")
    B.tell_memory(18810, task, 2, 3, OUT, "basic", wid)
    stats = {x["check"]: x for x in mem.fails.stats("basic")}
    assert set(stats) == {"test_add_and_list", "test_count"} and stats["test_count"]["seen"] == 1
    assert mem.db.execute("SELECT error FROM failures WHERE check_id = 'test_add_and_list'").fetchone()[0] == \
        "AssertionError: assert ['1. first'] == ['1. first item']"
    note = mem.db.execute("SELECT text FROM turns WHERE chat = 'acceptance-feedback'").fetchone()[0]
    assert "2/3 acceptance checks passed" in note and "first item" in note


def test_house_rules_are_unwritten_and_an_ordinary_solution_breaks_them(tmp_path):
    """The conventions set only measures learning if the goals never state the rules and a sensible solution that
    follows the goal alone fails them; the reference (which follows them) passes everything (self_check)."""
    house = T.all_tasks("house")
    assert len(house) == 17 and {t.family for t in house} == {"house-store", "house-report"}
    for t in house:
        assert not any(x in t.goal for x in ("(empty)", "error: ", "stderr", "exit", "\"items\"", "`;`"))
    reports = {t.id: t for t in T.all_tasks("basic") if t.family == "report"}
    for t in house:  # the basic set's solution of the same program: follows the goal, not the house rules
        (file,) = t.reference
        if t.family == "house-store":
            naive = T.STORE_REF.format(default="x.json", extra="")
        else:
            naive = next(iter(reports["report-" + file[len("summary_"):-3]].reference.values()))
        d = tmp_path / t.id
        d.mkdir()
        (d / file).write_text(naive)
        hp, ht = B.house_counts(t, B.score(t, d, None)[2])
        assert ht == 3 and hp < ht, t.id


def test_broken_rules_are_what_the_owner_points_out():
    rules = T.broken_rules(["test_add_and_list", "test_house_errors", "test_house_empty"])
    assert rules == [T.HOUSE_RULES["empty"], T.HOUSE_RULES["errors"]]
    assert T.broken_rules(["test_count"]) == []


def test_score_reads_each_house_check(tmp_path):
    t = T.all_tasks("house")[0]
    for name, content in t.reference.items():
        (tmp_path / name).write_text(content)
    passed, total, out = B.score(t, tmp_path, None)
    assert passed == total and B.house_counts(t, out) == (3, 3) and B.failed_tests(out) == []
    (tmp_path / "empty").mkdir()
    passed, total, out = B.score(t, tmp_path / "empty", None)
    assert passed == 0 and B.house_counts(t, out) == (0, 3) and "test_house_empty" in B.failed_tests(out)


def test_broken_house_rule_becomes_a_standing_rule_the_runner_sees(tmp_path):
    import threading
    import time
    sys.path[:0] = [str(Path(__file__).parent.parent / "memrouter"), str(Path(__file__).parent),
                    str(Path(__file__).parent.parent / "runner")]
    import longrun as LR
    import memory_service as M
    from test_hermes_memory import KeywordRanker, fake_encode
    mem = M.Memory(tmp_path, fake_encode, KeywordRanker())
    threading.Thread(target=M.serve, args=(mem, 18811), daemon=True).start()
    time.sleep(0.3)
    t = T.all_tasks("house")[0]
    out = "FAILED test_hidden_acceptance.py::test_house_errors - assert 1 == 2\n1 failed, 4 passed"
    B.tell_memory(18811, t, 4, 5, out, "house")
    rule = f"From now on, in every program you build for me: {T.HOUSE_RULES['errors']}"
    assert [r["text"] for r in mem.rules()] == [rule]  # stated by the owner: active at once
    mr = LR.Memrouter("http://127.0.0.1:18811", scope="house")
    text = mr.text(mr.read("Write summary_average.py, a CSV report"))
    assert text.startswith("OWNER'S STANDING RULES (always follow):\n- From now on") and "exits with code 2" in text
    assert "test_house_errors (failed 1x): assert 1 == 2" in text


def test_report_shows_house_rule_learning(tmp_path):
    for arm, hp in (("mem", [1, 2, 3, 3]), ("none", [1, 1, 1, 2])):
        rows = [{"arm": arm, "seed": 1, "pos": p, "task": f"t{p}", "family": "house-store", "passed": 2 + h,
                 "total": 5, "solved": h == 3, "minutes": 1.0, "interventions": 0, "runner_ok": True,
                 "house_passed": h, "house_total": 3} for p, h in enumerate(hp)]
        (tmp_path / f"{arm}-1.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    out = B.report(str(tmp_path))
    assert "house-rule checks passed  75.0%  (early half  50.0% -> late half 100.0%)" in out
    assert "house-rule checks passed  41.7%  (early half  33.3% -> late half  50.0%)" in out
    assert "mem - none, house-rule checks, paired on 4 jobs: +33.3 pts" in out
    assert "mem - none, house-rule checks, late half, paired on 2 jobs: +50.0 pts" in out


def test_report_compares_every_arm_with_no_memory(tmp_path):
    for arm, solved in (("mem", [1, 1, 0, 0]), ("rules", [1, 1, 1, 1]), ("none", [1, 0, 0, 0])):
        rows = [{"arm": arm, "seed": 1, "pos": p, "task": f"t{p}", "family": "x", "passed": s, "total": 1,
                 "solved": bool(s), "minutes": 1.0, "interventions": 0, "runner_ok": True} for p, s in enumerate(solved)]
        (tmp_path / f"{arm}-1.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    out = B.report(str(tmp_path))
    assert "mem - none, solved, paired on 4 jobs: +25.0 pts" in out
    assert "rules - none, solved, paired on 4 jobs: +75.0 pts" in out and "none - none" not in out
