from datetime import date

import pytest

import invoicing
from conftest import draft, issued, pin


def test_writing_off_closes_the_balance():
    invoice = issued(10000, when="2026-01-01T10:00:00Z", due_days=0)
    pin("2026-01-05T10:00:00Z")
    invoicing.record_payment(invoice, 4000, "upi")
    january = invoicing.payments_report(date(2026, 1, 1), date(2026, 1, 31))
    pin("2026-02-20T10:00:00Z")
    invoicing.write_off(invoice, "customer went bust")
    assert invoicing.get_invoice(invoice)["status"] == "written_off" and invoicing.balance_due(invoice) == 0
    later = date(2026, 6, 1)
    assert invoicing.overdue_invoices(later) == [] and invoicing.due_reminders(later) == []
    assert invoicing.apply_late_fees(later) == []
    assert invoicing.audit_log(invoice)[-1]["action"] == "written_off"
    assert invoicing.payments_report(date(2026, 1, 1), date(2026, 1, 31)) == january


def test_only_unpaid_issued_invoices_can_be_written_off():
    with pytest.raises(ValueError):
        invoicing.write_off(draft(), "x")
    paid = issued(100)
    invoicing.record_payment(paid, 100, "upi")
    with pytest.raises(ValueError):
        invoicing.write_off(paid, "x")
