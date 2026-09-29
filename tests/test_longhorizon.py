"""The long-horizon evaluation harness (evals/longhorizon), driven by fake `claude` binaries."""

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "evals" / "longhorizon"


def load(name):
    spec = importlib.util.spec_from_file_location(f"lh_{name}", EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EVAL))
    spec.loader.exec_module(module)
    return module


FAKE = '''#!{python}
"""A fake `claude -p`: {what}."""
import json, pathlib, shutil
repo = pathlib.Path.cwd()
{body}
print(json.dumps({{"type": "result", "subtype": "success", "result": "done", "num_turns": 3,
                  "total_cost_usd": 0.01, "usage": {{"input_tokens": 10, "output_tokens": 5}}}}))
'''


def fake(tmp_path, name, body, what):
    path = tmp_path / name
    path.write_text(FAKE.format(python=sys.executable, body=body, what=what))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.mark.parametrize("scenario", ["ledger", "notes"])
def test_reference_solutions_pass_every_hidden_test(scenario, tmp_path):
    """The hidden tests are passable: the reference implementation passes all of them, with no violations."""
    run = load("run")
    good = fake(tmp_path, "good", f"shutil.copytree({str(EVAL / 'reference' / scenario)!r}, repo / {scenario!r}, "
                                  "dirs_exist_ok=True)", "writes the reference solution")
    assert run.main(["--scenario", scenario, "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", good]) == 0
    result = json.loads((tmp_path / "out" / f"{scenario}-baseline" / "results.json").read_text())
    assert [r["session"] for r in result["sessions"]] == [1, 2, 3, 4]
    last = result["sessions"][-1]
    assert last["hidden"]["failed"] == [] and len(last["hidden"]["passed"]) == 4 and last["violations"] == []
    assert last["turns"] == 3 and last["cost_usd"] == 0.01


def test_constraint_drift_is_caught(tmp_path):
    """Floats, a third-party import, 3.10 syntax and a renamed function are each reported."""
    run = load("run")
    body = '''pkg = repo / "ledger"; pkg.mkdir(exist_ok=True)
(pkg / "__init__.py").write_text("""import requests
RATES = {"USD": 1.0}
def new_ledger(): return []
def add_expense(book, amount, category, note=""): book.append((amount / 100, category))
def total(ledger, category=None) -> float | None: return sum(a for a, c in ledger)
""")'''
    bad = fake(tmp_path, "bad", body, "drifts from every session-1 constraint")
    run.main(["--scenario", "ledger", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", bad,
              "--sessions", "1"])
    [row] = json.loads((tmp_path / "out" / "ledger-baseline" / "results.json").read_text())["sessions"]
    text = " ".join(row["violations"])
    assert "import requests" in text and "`X | Y` annotation" in text and "add_expense" in text
    assert row["hidden"]["failed"]  # 1349 cents came back as a float total


def test_horizon_arm_installs_horizon_and_the_report_compares(tmp_path, monkeypatch):
    run, report = load("run"), load("report")
    probe = fake(tmp_path, "probe", "assert (repo / '.mcp.json').exists() and (repo / 'CLAUDE.md').exists()",
                 "checks Horizon is installed")
    run.main(["--scenario", "notes", "--arm", "horizon", "--out", str(tmp_path / "out"), "--claude", probe,
              "--sessions", "2"])
    plain = fake(tmp_path, "plain", "assert not (repo / '.mcp.json').exists()", "checks the baseline has no Horizon")
    run.main(["--scenario", "notes", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", plain,
              "--sessions", "1"])
    report.main(["report", str(tmp_path / "out")])
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["notes/horizon"]["sessions_run"] == 2  # the probe passed: Horizon was installed
    assert summary["notes/baseline"]["sessions_run"] == 1 and summary["notes/baseline"]["aborted"] is False


def test_usage_limit_stops_the_scenario(tmp_path):
    run = load("run")
    limit = tmp_path / "limit"
    limit.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({{'type': 'result', 'result': "
                     "\"You've hit your session limit\"}))\n")
    limit.chmod(limit.stat().st_mode | stat.S_IEXEC)
    run.main(["--scenario", "ledger", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", str(limit)])
    rows = json.loads((tmp_path / "out" / "ledger-baseline" / "results.json").read_text())["sessions"]
    assert len(rows) == 1 and rows[0]["aborted"] == "usage limit"


def test_team_scenario_gives_each_project_a_fresh_repo(tmp_path):
    """Three projects, one fresh repo each: the reference passes every project's hidden tests, and a typical
    fresh-start implementation (dashed uuids, "+00:00" stamps, a print) fails the team's rules."""
    run = load("run")
    ref = EVAL / "reference" / "team"
    good = fake(tmp_path, "good", f"shutil.copytree({str(ref)!r} + '/' + repo.name, repo / repo.name)",
                "writes the reference for this project")
    run.main(["--scenario", "team", "--arm", "baseline", "--out", str(tmp_path / "ok"), "--claude", good])
    rows = json.loads((tmp_path / "ok" / "team-baseline" / "results.json").read_text())["sessions"]
    assert [(len(r["hidden"]["passed"]), r["hidden"]["failed"], r["violations"]) for r in rows] == [(4, [], [])] * 3
    assert sorted(p.name for p in (tmp_path / "ok" / "team-baseline").iterdir() if p.is_dir()) == \
        ["invoices", "payouts", "subscriptions"]

    body = '''pkg = repo / "invoices"; pkg.mkdir()
(pkg / "__init__.py").write_text("""import json, uuid
from datetime import datetime, timezone
def create_invoice(customer, amount_cents, due):
    print("creating invoice")
    return {"id": str(uuid.uuid4()), "customer": customer, "amount_cents": amount_cents,
            "created_at": datetime.now(timezone.utc).isoformat(), "due": due.isoformat(), "status": "open"}
def overdue(invoices, now): return []
def to_json(i): return json.dumps(i)
def from_json(t): return json.loads(t)
""")'''
    drift = fake(tmp_path, "drift", body, "ignores the team's rules")
    run.main(["--scenario", "team", "--arm", "baseline", "--out", str(tmp_path / "bad"), "--claude", drift,
              "--sessions", "1"])
    [row] = json.loads((tmp_path / "bad" / "team-baseline" / "results.json").read_text())["sessions"]
    assert set(row["hidden"]["failed"]) == {"test_invoice_follows_the_team_rules", "test_overdue_compares_real_instants",
                                            "test_bad_amount_raises_value_error"}
