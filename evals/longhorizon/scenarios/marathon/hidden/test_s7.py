import re

import pytest

import invoicing
from conftest import HEX, draft, is_cents, issued, pin


def test_credit_notes_reduce_the_balance():
    invoice = issued(10000)
    invoicing.record_payment(invoice, 3000, "upi")
    pin("2026-03-04T10:00:00Z")
    credit = invoicing.credit_note(invoice, 2000, "damaged")
    assert re.fullmatch(HEX, credit["id"]) and credit["invoice_id"] == invoice
    assert (credit["amount_cents"], credit["reason"]) == (2000, "damaged") and credit["created_at"].endswith("Z")
    assert invoicing.balance_due(invoice) == 5000 and is_cents(invoicing.balance_due(invoice))
    with pytest.raises(ValueError):
        invoicing.record_payment(invoice, 5001, "upi")
    invoicing.credit_note(invoice, 5000, "goodwill")
    assert invoicing.balance_due(invoice) == 0 and invoicing.get_invoice(invoice)["status"] == "paid"


def test_bad_credit_notes_are_refused():
    invoice = issued(10000)
    for bad in (0, -1, 10001):
        with pytest.raises(ValueError):
            invoicing.credit_note(invoice, bad, "x")
    with pytest.raises(ValueError):
        invoicing.credit_note(draft(), 100, "x")
    assert invoicing.balance_due(invoice) == 10000
