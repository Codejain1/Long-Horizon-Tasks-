"""Experience keyed by what failed: real outcomes in, concrete warnings out, and whether a warning helped."""

import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent.parent / "memrouter"), str(Path(__file__).parent)]
import failures as FL  # noqa: E402
from test_memory_service import memory  # noqa: E402

OUT = """FAILED test_hidden_acceptance.py::test_house_errors - AssertionError: assert 'error: ' in ''
FAILED test_hidden_acceptance.py::test_house_empty - assert '' == '(empty)'
ERROR test_hidden_acceptance.py::test_count
PASSED test_hidden_acceptance.py::test_add_and_list
3 failed, 1 passed"""


def test_pytest_summary_becomes_checks_with_their_message():
    assert FL.from_pytest(OUT) == [{"check": "test_house_errors", "error": "AssertionError: assert 'error: ' in ''"},
                                   {"check": "test_house_empty", "error": "assert '' == '(empty)'"},
                                   {"check": "test_count", "error": ""}]


def test_warnings_rank_recurring_failures_and_stay_in_their_scope(tmp_path):
    f = memory(tmp_path).fails
    f.record("house", FL.from_pytest(OUT))
    f.record("house", [{"check": "test_house_errors", "error": "assert 2 == 1"}])
    f.record("rental", [{"check": "test_deposit", "error": "assert 100 == 400"}])
    warned, wid = f.warn("house")
    assert [w["check"] for w in warned][:1] == ["test_house_errors"] and warned[0]["seen"] == 2
    assert warned[0]["error"] == "assert 2 == 1"  # the latest message
    assert "test_deposit" not in [w["check"] for w in warned] and wid
    assert f.warn("") == ([], None) and f.warn("ward") == ([], None)


def test_the_next_outcome_says_whether_a_warning_helped(tmp_path):
    f = memory(tmp_path).fails
    f.record("house", FL.from_pytest(OUT))
    _, wid = f.warn("house")
    out = f.record("house", [{"check": "test_house_empty", "error": "still"}], ["test_house_errors"], wid)
    assert sorted(out["credited"]) == ["test_count", "test_house_empty", "test_house_errors"]
    stats = {s["check"]: s for s in f.stats("house")}
    assert stats["test_house_errors"]["then_passed"] == 1 and stats["test_house_empty"]["then_passed"] == 0
    assert stats["test_house_empty"]["seen"] == 2 and stats["test_house_errors"]["warned"] == 1
    assert f.record("house", [], [], wid)["credited"] == []  # a warning is credited once


def test_search_carries_the_scope_warnings(tmp_path):
    mem = memory(tmp_path)
    mem.add("hello", "owner", "c")
    mem.fails.record("house", [{"check": "test_house_errors", "error": "exit code 2"}])
    r = mem.search("write a store program", 300, scope="house")
    assert r["warnings"][0]["check"] == "test_house_errors" and r["warning_id"]
    assert mem.search("write a store program", 300)["warnings"] == []
