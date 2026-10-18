"""Tickets one at a time: each state passes the tickets before it and leaves its own ticket open; the report."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
import backlog as BL  # noqa: E402
import tickets as TK  # noqa: E402


def test_states_pass_earlier_tickets_and_leave_their_own_open(tmp_path):
    for name, ks in (("rental", [1, 2, 3, 4]), ("ward", [1, 2, 11])):
        sdir, sc = BL.scenario(name)
        for k in ks:
            ok, why = TK.check_state(sdir, sc, k, tmp_path / name)
            assert ok, why
    sdir, sc = BL.scenario("rental")
    repo = TK.build_state(sdir, sc, 3, tmp_path / "r3")
    assert (repo / "rentals" / "kyc.py").exists() and not (repo / "rentals" / "visits.py").exists()
    assert "visits" not in (repo / "rentals" / "__init__.py").read_text()
    assert (repo / "tests").exists() and not (repo / "docs").exists()  # the start's tests, nothing about later tickets


def test_a_state_that_already_solves_its_ticket_is_rejected(tmp_path):
    sdir, sc = BL.scenario("rental")
    ok, why = TK.check_state(sdir, sc, 5, tmp_path)
    assert not ok and "ALREADY SOLVED" in why


def test_report_rates_intervals_and_pairs(tmp_path):
    rows = [{"ticket": k, "arm": arm, "rep": r, "solved": arm == "runner" or k == 1, "kept": True, "minutes": 1,
             "tokens": 10} for k in (1, 2, 3) for r in (1, 2, 3) for arm in ("plain", "runner")]
    rows.append({"ticket": 1, "arm": "runner", "rep": 4, "error": "RuntimeError('the runner crashed')"})
    (tmp_path / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = TK.report(str(tmp_path))
    assert "runner  solved 9/9 =  100% [70, 100]" in out and "plain   solved 3/9" in out
    assert "solved only by runner 6, only by plain 0 (of 9 pairs)" in out and "HARNESS ERRORS (1" in out
    assert TK.tickets("1-3,6,11") == [1, 2, 3, 6, 11] and TK.wilson(0, 0) == (0.0, 1.0)
