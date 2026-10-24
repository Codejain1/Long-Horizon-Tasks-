import csv
from datetime import date

from conftest import funded, home, owner, tenant

import rentals


def _statement(tmp_path):
    o = owner()
    a = funded(home(by=o), who=tenant("Arjun", "9123456789"))
    rentals.pay_rent(a, date(2026, 1, 1))
    rentals.pay_rent(a, date(2026, 2, 1))
    rentals.set_today(date(2026, 3, 1))
    path = tmp_path / "statement.csv"
    rentals.owner_statement_csv(o, str(path))
    with open(path, newline="") as f:
        return list(csv.DictReader(f)), a


def test_the_statement_lists_each_agreement(tmp_path):
    rows, a = _statement(tmp_path)
    assert [r["agreement_id"] for r in rows] == [a]
    assert rows[0]["tenant_name"] == "Arjun" and int(rows[0]["rent_paid_cents"]) == 5000000
    assert int(rows[0]["yield_cents"]) == rentals.yield_split(a, date(2026, 3, 1))["owner_cents"]


def test_the_statement_masks_tenant_phones(tmp_path):
    """Ticket 2's rule; ticket 11 doesn't repeat it."""
    rows, _ = _statement(tmp_path)
    assert rows[0]["tenant_phone"] == "91******89"
