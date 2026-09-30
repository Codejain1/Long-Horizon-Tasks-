import pytest

import invoicing
from conftest import at, draft, pin


def test_issuing_numbers_the_invoice_and_sets_utc_dates():
    pin("2026-03-31T20:00:00Z")  # 01:30 on 1 April in India: the due date is still a UTC day
    first = draft()
    invoicing.issue_invoice(first, due_days=0)
    got = invoicing.get_invoice(first)
    assert (got["status"], got["number"], got["due_on"]) == ("issued", "INV-0001", "2026-03-31")
    assert got["issued_at"].endswith("Z") and at(got["issued_at"]) == at("2026-03-31T20:00:00Z")
    second = draft()
    invoicing.issue_invoice(second)
    got = invoicing.get_invoice(second)
    assert (got["number"], got["due_on"]) == ("INV-0002", "2026-04-30")
    with pytest.raises(ValueError):
        invoicing.issue_invoice(first)


def test_numbering_starts_again_after_reset():
    invoicing.issue_invoice(draft())
    invoicing.reset()
    invoice = draft()
    invoicing.issue_invoice(invoice)
    assert invoicing.get_invoice(invoice)["number"] == "INV-0001"
