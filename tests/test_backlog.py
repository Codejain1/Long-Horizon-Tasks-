"""The backlog benchmark: tickets in one repo, hidden tests after each, QA fix-ups, the notes arm, the report."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
import backlog as BL  # noqa: E402


def scenario(root: Path) -> None:
    """Ticket 1 adds price(); its note says prices are in whole rupees. Ticket 2 adds total(), which needs that rule."""
    d = root / "scenarios" / "shop"
    (d / "start" / "shop").mkdir(parents=True)
    (d / "start" / "shop" / "__init__.py").write_text("")
    (d / "hidden").mkdir()
    (d / "hidden" / "test_s1.py").write_text("import shop\n\ndef test_price():\n    assert shop.price(3) == 30\n")
    (d / "hidden" / "test_s2.py").write_text("import shop\n\ndef test_total_is_whole_rupees():\n"
                                             "    assert shop.total([1, 2]) == 30\n")
    (d / "scenario.json").write_text(json.dumps({
        "sessions": ["Ticket #1: price(n) = 10 per item. Note for later: totals are whole rupees.",
                     "Ticket #2: total(items) sums price over the items."],
        "rule_checks": {"test_total_is_whole_rupees": "whole rupees"}}))


def args(tmp_path, arm, **kw):
    return SimpleNamespace(scenario="shop", arm=arm, out=str(tmp_path / "out"), model_url="x", tickets=99, fixups=2,
                           max_minutes=1, image=None, local=True, port=0, memrouter_cmd="", **kw)


def test_tickets_score_hidden_tests_and_qa_fixups_count(tmp_path, monkeypatch):
    scenario(tmp_path)
    monkeypatch.setattr(BL, "HERE", tmp_path)
    goals = []

    def job(goal, repo, logs, tag, a, port):  # gets ticket 1 right; forgets ticket 2 until QA says what fails
        goals.append((tag, goal))
        code = "def price(n):\n    return 10 * n\n"
        if "fix" in tag:
            code += "def total(items):\n    return sum(price(n) for n in items)\n"
        (repo / "shop" / "__init__.py").write_text(code)
        return {"minutes": 1.0, "ok": True, "tokens": 100}
    rows = BL.run(args(tmp_path, "runner"), job=job)
    assert [(r["ticket"], r["after_build"], r["final"], r["total"], r["fixup_rounds"]) for r in rows] == \
        [(1, 1, 1, 1, 0), (2, 1, 2, 2, 1)]
    assert rows[1]["rules_passed"] == ["test_total_is_whole_rupees"]
    qa = goals[-1][1]
    assert qa.startswith("QA report after ticket #2") and "test_total_is_whole_rupees" in qa
    assert "test_s2" not in qa and "whole rupees" not in qa  # names the failing check, never the test code or old ticket
    out = BL.report(str(tmp_path / "out"))
    assert "runner  tickets  2  hidden at the end  2/ 2  rule checks 1/1" in out and "fix-up rounds  1" in out


def test_notes_arm_keeps_every_ticket_and_report_in_the_repo(tmp_path, monkeypatch):
    scenario(tmp_path)
    monkeypatch.setattr(BL, "HERE", tmp_path)
    seen = []

    def job(goal, repo, logs, tag, a, port):
        seen.append(goal)
        return {"minutes": 0.5, "ok": False, "tokens": 0}
    BL.run(args(tmp_path, "notes", ), job=job)
    notes = (tmp_path / "out" / "notes" / "repo" / "NOTES.md").read_text()
    assert "Ticket #1" in notes and "whole rupees" in notes and "QA report after ticket #2" in notes
    assert all(g.endswith(BL.NOTES_HINT) for g in seen)


def test_real_scenarios_are_whole_and_start_unsolved(tmp_path):
    for name in ("rental", "ward"):
        sdir, sc = BL.scenario(name)
        assert len(list(sdir.glob("hidden/test_s*.py"))) == len(sc["sessions"])
        passed, failed, _ = BL.hidden(sdir, len(sc["sessions"]), sdir / "start", tmp_path / name, None)
        assert passed == [] and len(failed) >= 23 and set(sc["rule_checks"]) <= set(failed), name
