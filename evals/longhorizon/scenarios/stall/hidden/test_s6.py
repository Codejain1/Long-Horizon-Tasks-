from datetime import date

import invoicing
from conftest import ids, issued


def test_overdue_invoices_use_utc_days():
    a = issued(when="2026-03-01T10:00:00Z", due_days=10)  # due 11 March
    late = issued(when="2026-03-31T20:00:00Z", due_days=0)  # already 1 April in India; due 31 March (UTC)
    paid = issued(when="2026-03-01T11:00:00Z", due_days=5)
    invoicing.record_payment(paid, 10000, "upi")
    part = issued(when="2026-03-01T12:00:00Z", due_days=20)  # due 21 March
    invoicing.record_payment(part, 100, "upi")
    assert ids(invoicing.overdue_invoices(date(2026, 4, 1))) == [a, part, late]
    assert ids(invoicing.overdue_invoices(date(2026, 3, 31))) == [a, part]
    assert invoicing.overdue_invoices(date(2026, 3, 11)) == []
