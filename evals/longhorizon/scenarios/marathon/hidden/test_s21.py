from datetime import date

import pytest

import invoicing
from conftest import customer, ids, issued, pin, product


def test_merging_moves_invoices_and_schedules():
    keep, gone = customer("Asha"), customer("Asha Duplicate")
    invoice = issued(1000, customer_id=gone)
    invoicing.create_schedule(gone, [(product(500), 1)], 30, date(2026, 3, 1))
    invoicing.merge_customers(keep, gone)
    with pytest.raises(KeyError):
        invoicing.get_customer(gone)
    assert invoicing.get_invoice(invoice)["customer_id"] == keep
    assert ids(invoicing.list_invoices(keep)) == [invoice]
    assert invoicing.audit_log(invoice)[-1]["action"] == "customer_merged"
    pin("2026-03-01T12:00:00Z")
    assert [i["customer_id"] for i in invoicing.run_schedules(date(2026, 3, 1))] == [keep]


def test_a_customer_cant_be_merged_into_itself():
    c = customer()
    with pytest.raises(ValueError):
        invoicing.merge_customers(c, c)
    assert invoicing.get_customer(c)["id"] == c
