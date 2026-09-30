import re
from datetime import date

import invoicing
from conftest import HEX, customer, pin, product


def test_schedules_issue_every_occurrence_once():
    c = customer()
    schedule = invoicing.create_schedule(c, [(product(1500), 2)], 14, date(2026, 3, 1))
    assert re.fullmatch(HEX, schedule["id"])
    pin("2026-03-20T09:00:00Z")
    made = invoicing.run_schedules(date(2026, 3, 20))  # 1 and 15 March
    assert [i["number"] for i in made] == ["INV-0001", "INV-0002"]
    assert all(i["status"] == "issued" and i["customer_id"] == c for i in made)
    assert invoicing.invoice_total(made[0]["id"])["total_cents"] == 3000
    assert invoicing.get_invoice(made[0]["id"])["due_on"] == "2026-04-19"
    assert invoicing.run_schedules(date(2026, 3, 20)) == []
    assert len(invoicing.run_schedules(date(2026, 3, 29))) == 1  # 29 March
    assert invoicing.run_schedules(date(2026, 4, 11)) == []
