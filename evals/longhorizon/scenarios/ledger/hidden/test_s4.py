import subprocess
import sys

import ledger


def test_budgets_and_report(tmp_path):
    lg = ledger.new_ledger()
    ledger.add_expense(lg, 6000, "food")
    ledger.add_expense(lg, 1000, "fun", currency="EUR")
    ledger.set_budget(lg, "food", 5000)
    ledger.set_budget(lg, "fun", 2000)
    ledger.set_budget(lg, "rent", 100000)
    assert ledger.over_budget(lg) == ["food"]
    path = tmp_path / "l.dat"
    ledger.save(lg, path)
    out = subprocess.run([sys.executable, "-m", "ledger", "report", str(path)], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert "food" in out.stdout and "fun" in out.stdout
