from datetime import date

import invoicing
from conftest import customer, issued


def test_aging_buckets_per_customer_in_inr():
    invoicing.set_fx_rate("USD", "80")
    asha, ravi, settled = customer("Asha"), customer("Ravi"), customer("Settled")
    issued(1000, when="2026-06-20T10:00:00Z", due_days=30, customer_id=asha)  # due 20 July: current
    issued(50, when="2026-06-30T10:00:00Z", due_days=0, customer_id=asha)  # due today: current
    part = issued(2000, when="2026-06-20T10:00:00Z", due_days=0, customer_id=asha)  # 10 days late
    invoicing.record_payment(part, 500, "upi")
    issued(70, when="2026-05-31T10:00:00Z", due_days=0, customer_id=asha)  # 30 days
    issued(3000, when="2026-05-16T10:00:00Z", due_days=0, customer_id=asha)  # 45 days
    issued(4000, when="2026-04-30T10:00:00Z", due_days=0, customer_id=ravi)  # 61 days
    issued(100, when="2026-03-02T10:00:00Z", due_days=0, customer_id=ravi, currency="USD")  # 120 days: 8000
    paid = issued(999, when="2026-03-02T10:00:00Z", due_days=0, customer_id=settled)
    invoicing.record_payment(paid, 999, "upi")
    assert invoicing.aging_report(date(2026, 6, 30)) == {
        asha: {"current": 1050, "1-30": 1570, "31-60": 3000, "61-90": 0, "90+": 0},
        ravi: {"current": 0, "1-30": 0, "31-60": 0, "61-90": 4000, "90+": 8000},
    }
