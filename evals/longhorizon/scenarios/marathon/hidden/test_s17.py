import re

import pytest

import invoicing
from conftest import HEX, ids, issued, pin


def test_refunds_reopen_the_balance():
    invoice = issued(10000)
    pin("2026-03-02T10:00:00Z")
    payment = invoicing.record_payment(invoice, 10000, "card")
    assert invoicing.get_invoice(invoice)["status"] == "paid"
    pin("2026-03-03T10:00:00Z")
    refund = invoicing.refund_payment(payment["id"], 2500, "returned")
    assert re.fullmatch(HEX, refund["id"]) and refund["payment_id"] == payment["id"]
    assert (refund["amount_cents"], refund["reason"]) == (2500, "returned") and refund["created_at"].endswith("Z")
    assert invoicing.get_invoice(invoice)["status"] == "partially_paid" and invoicing.balance_due(invoice) == 2500
    assert ids(invoicing.refunds_for(invoice)) == [refund["id"]]
    invoicing.record_payment(invoice, 2500, "upi")
    assert invoicing.get_invoice(invoice)["status"] == "paid"


def test_refunds_are_limited_to_what_is_left_of_the_payment():
    invoice = issued(10000)
    payment = invoicing.record_payment(invoice, 4000, "upi")
    invoicing.refund_payment(payment["id"], 3000, "partial")
    for bad in (0, -1, 1001):
        with pytest.raises(ValueError):
            invoicing.refund_payment(payment["id"], bad, "x")
    assert invoicing.balance_due(invoice) == 9000
