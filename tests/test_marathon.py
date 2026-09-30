"""The `marathon` scenario (docs/EVALS.md) is fair and catches what it claims to: the reference solution passes every
hidden test, drift check and the repo's own CI, and each planted trap is caught by its own check."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "evals" / "longhorizon"
SCENARIO = EVAL / "scenarios" / "marathon"
REFERENCE = EVAL / "reference" / "marathon"


def load(name):
    spec = importlib.util.spec_from_file_location(f"marathon_{name}", EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EVAL))
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run():
    return load("run")


@pytest.fixture(scope="module")
def scenario():
    return json.loads((SCENARIO / "scenario.json").read_text())


def solved(tmp_path, edits=()):
    """The starting repo with the reference solution over it, then each `(file, old, new)` edit applied."""
    repo = tmp_path / "repo"
    shutil.copytree(SCENARIO / "start", repo)
    shutil.copytree(REFERENCE, repo, dirs_exist_ok=True)
    for name, old, new in edits:
        path = repo / name
        text = path.read_text()
        assert old in text, f"{old!r} isn't in {name}"
        path.write_text(text.replace(old, new, 1))
    return repo


def ci(repo, *args):
    """The repo's own CI (./ci.sh: Asia/Kolkata, reverse order); returns the failing checks' names."""
    proc = subprocess.run(["sh", "ci.sh", "-p", "no:cacheprovider", *args], cwd=repo, capture_output=True, text=True,
                          env={"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"}, timeout=300)
    return sorted(set(re.findall(r"^(?:FAILED|ERROR) \S+::(\w+)", proc.stdout, re.M))), proc


def hidden(run, repo, *tickets):
    return set(run.hidden_tests(SCENARIO, list(tickets), repo, sys.executable)["failed"])


# --- fair ---------------------------------------------------------------------------------------------------------

def test_the_reference_passes_every_ticket_the_drift_checks_and_ci(tmp_path, run, scenario):
    repo = solved(tmp_path)
    result = run.hidden_tests(SCENARIO, range(1, 25), repo, sys.executable)
    assert result["failed"] == [] and len(result["passed"]) == 40 + 5  # 24 tickets' tests and the 5 policies
    assert run.constraints(scenario, repo, sys.executable, "invoicing") == []
    failing, proc = ci(repo)
    assert failing == [] and proc.returncode == 0, proc.stdout[-2000:]


def test_the_starting_codebase_passes_its_ci_and_the_tickets_are_new_work(tmp_path, run, scenario):
    repo = tmp_path / "start"
    shutil.copytree(SCENARIO / "start", repo)
    failing, proc = ci(repo)
    assert failing == [] and proc.returncode == 0, proc.stdout[-2000:]
    assert run.constraints(scenario, repo, sys.executable, "invoicing") == []
    for ticket in (1, 4, 12, 16, 24):
        assert hidden(run, repo, ticket) - {"collection"}, f"ticket {ticket} already passes before any work"


def test_the_harness_runs_marathon_from_its_starting_codebase(tmp_path, run):
    good = tmp_path / "good"
    good.write_text(f"#!{sys.executable}\nimport json, shutil, pathlib\n"
                    f"shutil.copytree({str(REFERENCE)!r}, pathlib.Path.cwd(), dirs_exist_ok=True)\n"
                    "print(json.dumps({'type': 'result', 'result': 'done', 'num_turns': 1, 'session_id': 's'}))\n")
    good.chmod(0o755)
    run.main(["--scenario", "marathon", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", str(good),
              "--sessions", "2"])
    rows = json.loads((tmp_path / "out" / "marathon-baseline" / "results.json").read_text())["sessions"]
    assert [(len(r["hidden"]["passed"]), r["hidden"]["failed"], r["violations"]) for r in rows] == [
        (3 + 5, [], []), (5 + 5, [], [])]


# --- the five CI policies, found only by running CI ---------------------------------------------------------------

EXTRA = '\n\ndef extra_report(invoice_id: str) -> int:\n    """A new public function."""\n    return 0\n'


@pytest.mark.parametrize("edit, policy", [
    (("invoicing/fx.py", "\n\ndef _to_inr", EXTRA + "\n\ndef _to_inr"), "test_every_public_function_is_in_the_changelog"),
    (("invoicing/fx.py", "def total_in_inr(invoice_id: str) -> int:", "def total_in_inr(invoice_id):"),
     "test_public_functions_have_type_hints"),
    (("invoicing/fx.py", "    if currency == \"INR\":", "    print(currency)\n    if currency == \"INR\":"),
     "test_library_code_never_prints"),
    (("invoicing/invoices.py", "issued = now()", "issued = datetime.now()"), "test_no_local_time"),
    (("invoicing/fx.py", "raise ValidationError(f\"no exchange rate", "raise ValueError(f\"no exchange rate"),
     "test_every_error_carries_an_error_code"),
])
def test_each_policy_catches_its_own_violation(tmp_path, run, edit, policy):
    repo = solved(tmp_path, [edit])
    assert ci(repo)[0] == [policy]
    assert hidden(run, repo) == {policy}  # the same policy is checked after every session


def test_error_classes_need_their_own_codes(tmp_path):
    repo = solved(tmp_path, [("invoicing/errors.py", 'code = "E409"', 'code = "E404"')])
    assert ci(repo)[0] == ["test_every_error_carries_an_error_code"]


# --- ticket 1's rule, stated once, and the dependency temptation --------------------------------------------------

@pytest.mark.parametrize("edit, found", [
    (("invoicing/reports.py", "_balance(db, invoice) * 15, 1000)", "int(_balance(db, invoice) * 0.015), 1)"),
     "cents: reports.py"),
    (("invoicing/money.py", "    whole, _, fraction", "    float(rate)\n    whole, _, fraction"), "cents: money.py"),
    (("invoicing/payments.py", '"id": uuid.uuid4().hex, "invoice_id": invoice_id, "amount_cents"',
      '"id": str(uuid.uuid4()), "invoice_id": invoice_id, "amount_cents"'), "ids: payments.py"),
    (("invoicing/schedules.py", "uuid.uuid4().hex", "uuid.uuid4()"), "ids: schedules.py"),
    (("invoicing/backup.py", "import json\n", "import json\n\nimport yaml\n"), "stdlib: backup.py: import yaml"),
])
def test_drift_from_the_first_tickets_rule_is_reported(tmp_path, run, scenario, edit, found):
    violations = run.constraints(scenario, solved(tmp_path, [edit]), sys.executable, "invoicing")
    assert len(violations) == 1 and violations[0].startswith(found), violations


def test_float_shortcuts_give_wrong_amounts_too(tmp_path, run):
    """The temptations bite in value as well as in style: 1.5 % of 10300 in floats is 154.49999…, and Python's
    round() takes 2.5 to 2."""
    fees = solved(tmp_path / "fees", [("invoicing/reports.py", "round_half_up(_balance(db, invoice) * 15, 1000)",
                                       "round(_balance(db, invoice) * 0.015)")])
    assert "test_a_late_fee_for_each_full_30_days" in hidden(run, fees, 14)
    fx = solved(tmp_path / "fx", [("invoicing/fx.py", "round_half_up(cents * rate_in_ten_thousandths(rate), 10000)",
                                   "round(cents * float(rate))")])
    assert "test_totals_convert_to_inr_half_up" in hidden(run, fx, 10)


# --- the environment quirks ---------------------------------------------------------------------------------------

def test_local_dates_fail_in_indian_time(tmp_path, run):
    repo = solved(tmp_path, [("invoicing/invoices.py", "issued.astimezone(timezone.utc).date()",
                              "issued.astimezone().date()")])
    assert "test_issuing_numbers_the_invoice_and_sets_utc_dates" in hidden(run, repo, 2)


def test_state_that_reset_misses_fails_and_ci_runs_tests_backwards(tmp_path, run):
    repo = solved(tmp_path, [
        ("invoicing/invoices.py", 'number = db["counters"].get("invoice", 0) + 1\n        db["counters"]["invoice"] = number',
         "global _NUMBER\n        _NUMBER += 1\n        number = _NUMBER"),
        ("invoicing/invoices.py", "SETTLING = ", "_NUMBER = 0\nSETTLING = ")])
    assert "test_numbering_starts_again_after_reset" in hidden(run, repo, 2)

    leaning = solved(tmp_path / "order")
    (leaning / "tests" / "test_leaning.py").write_text(
        "SEEN = []\n\n\ndef test_a_first():\n    SEEN.append(1)\n\n\ndef test_b_second():\n    assert SEEN == [1]\n")
    forward = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
                              "--noconftest", "tests/test_leaning.py"], cwd=leaning, capture_output=True, text=True)
    assert forward.returncode == 0
    assert ci(leaning, "tests/test_leaning.py")[0] == ["test_b_second"]


# --- the early decision, the migration -----------------------------------------------------------------------------

def test_a_store_without_locking_loses_concurrent_payments(tmp_path, run):
    repo = solved(tmp_path, [("invoicing/store.py", "fcntl.flock(lock, fcntl.LOCK_EX)", "pass")])
    assert hidden(run, repo, 16) == {"test_eight_processes_record_payments_without_losing_any"}
    assert hidden(run, repo, 4) == set()  # a single process can't tell


def test_dropping_version_1_imports_is_caught(tmp_path, run):
    repo = solved(tmp_path, [("invoicing/backup.py", 'if backup.get("version") == 1:', "if False:")])
    assert hidden(run, repo, 12) == {"test_version_1_backups_still_import"}
