from datetime import date

import invoicing
from conftest import issued


def test_a_late_fee_for_each_full_30_days():
    invoice = issued(10300, when="2026-01-01T10:00:00Z", due_days=0)  # due 1 January
    assert invoicing.apply_late_fees(date(2026, 1, 30)) == []  # 29 days
    assert invoicing.apply_late_fees(date(2026, 1, 31)) == [{"invoice_id": invoice, "cents": 155}]  # 154.5, half up
    assert invoicing.apply_late_fees(date(2026, 1, 31)) == []  # never twice for one period
    total = invoicing.invoice_total(invoice)
    assert (total["late_fees_cents"], total["tax_cents"], total["total_cents"]) == (155, 0, 10455)
    assert invoicing.balance_due(invoice) == 10455
    assert invoicing.apply_late_fees(date(2026, 3, 2)) == [{"invoice_id": invoice, "cents": 157}]  # 60 days: 156.825


def test_fees_skip_paid_invoices_and_come_one_period_at_a_time():
    paid = issued(1000, when="2026-01-01T10:00:00Z", due_days=0)
    invoicing.record_payment(paid, 1000, "upi")
    part = issued(10000, when="2026-01-01T11:00:00Z", due_days=0)
    invoicing.record_payment(part, 2000, "upi")
    assert invoicing.apply_late_fees(date(2026, 3, 2)) == [{"invoice_id": part, "cents": 120},
                                                           {"invoice_id": part, "cents": 122}]  # 8000, then 8120
    assert (invoicing.balance_due(part), invoicing.balance_due(paid)) == (8242, 0)
