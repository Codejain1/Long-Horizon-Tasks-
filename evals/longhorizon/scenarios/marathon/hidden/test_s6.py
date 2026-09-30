from datetime import date

import pytest

import invoicing
from conftest import at, draft, issued, pin


def test_void_an_issued_invoice():
    invoice = issued()
    pin("2026-03-05T08:00:00Z")
    invoicing.void_invoice(invoice, "duplicate")
    got = invoicing.get_invoice(invoice)
    assert (got["status"], got["void_reason"]) == ("void", "duplicate")
    assert got["voided_at"].endswith("Z") and at(got["voided_at"]) == at("2026-03-05T08:00:00Z")
    with pytest.raises(ValueError):
        invoicing.void_invoice(invoice, "again")
    with pytest.raises(ValueError):
        invoicing.record_payment(invoice, 100, "upi")
    assert invoicing.overdue_invoices(date(2026, 6, 1)) == []


def test_only_unpaid_issued_invoices_can_be_voided():
    with pytest.raises(ValueError):
        invoicing.void_invoice(draft(), "x")
    invoice = issued()
    invoicing.record_payment(invoice, 100, "upi")
    with pytest.raises(ValueError):
        invoicing.void_invoice(invoice, "x")
    assert invoicing.get_invoice(invoice)["status"] == "partially_paid"
