from datetime import date

import invoicing
from conftest import issued


def test_reminders_at_7_14_and_30_days_each_once():
    a = issued(when="2026-03-01T10:00:00Z", due_days=0)  # due 1 March
    b = issued(when="2026-03-10T10:00:00Z", due_days=0)  # due 10 March
    paid = issued(when="2026-03-01T11:00:00Z", due_days=0)
    invoicing.record_payment(paid, 10000, "upi")
    assert invoicing.due_reminders(date(2026, 3, 7)) == []  # 6 days
    assert invoicing.due_reminders(date(2026, 3, 21)) == [
        {"invoice_id": a, "days": 7}, {"invoice_id": a, "days": 14}, {"invoice_id": b, "days": 7}]
    assert invoicing.due_reminders(date(2026, 3, 21)) == []
    assert invoicing.due_reminders(date(2026, 3, 31)) == [{"invoice_id": a, "days": 30}, {"invoice_id": b, "days": 14}]
