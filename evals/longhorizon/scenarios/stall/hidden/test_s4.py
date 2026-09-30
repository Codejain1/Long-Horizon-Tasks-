import re

import pytest

import invoicing
from conftest import HEX, draft, issued, pin


def test_payments_move_an_invoice_to_partially_paid_then_paid():
    invoice = issued(10000)
    pin("2026-03-02T10:00:00Z")
    payment = invoicing.record_payment(invoice, 4000, "upi")
    assert re.fullmatch(HEX, payment["id"]) and payment["invoice_id"] == invoice
    assert (payment["amount_cents"], payment["method"]) == (4000, "upi") and payment["paid_at"].endswith("Z")
    assert invoicing.get_invoice(invoice)["status"] == "partially_paid"
    pin("2026-03-03T10:00:00Z")
    invoicing.record_payment(invoice, 6000, "card")
    assert invoicing.get_invoice(invoice)["status"] == "paid"
    assert [p["amount_cents"] for p in invoicing.payments_for(invoice)] == [4000, 6000]


def test_bad_payments_are_refused():
    with pytest.raises(ValueError):
        invoicing.record_payment(draft(), 100, "upi")
    invoice = issued(10000)
    for bad in (0, -5, 10001):
        with pytest.raises(ValueError):
            invoicing.record_payment(invoice, bad, "upi")
    invoicing.record_payment(invoice, 9000, "upi")
    with pytest.raises(ValueError):
        invoicing.record_payment(invoice, 1001, "upi")
    assert [p["amount_cents"] for p in invoicing.payments_for(invoice)] == [9000]
