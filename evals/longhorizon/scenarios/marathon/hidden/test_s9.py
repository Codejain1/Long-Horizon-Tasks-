import pytest

import invoicing
from conftest import customer, draft, issued


def test_discounts_come_off_each_line_before_tax_half_up():
    tea = invoicing.add_product("Tea", 333)  # 18 %
    invoice = invoicing.create_invoice(customer(), [(tea["id"], 3)])["id"]
    invoicing.apply_discount(invoice, 10)  # 999 - 99.9 (100) = 899; tax 161.82 -> 162
    total = invoicing.invoice_total(invoice)
    assert (total["subtotal_cents"], total["tax_cents"], total["total_cents"]) == (899, 162, 1061)


def test_half_a_cent_of_discount_rounds_up():
    invoice = draft(25)
    invoicing.apply_discount(invoice, 50)  # 12.5 off -> 13 off
    assert invoicing.invoice_total(invoice)["total_cents"] == 12


def test_only_drafts_take_a_whole_percentage():
    invoice = draft(1000)
    for bad in (-1, 101):
        with pytest.raises(ValueError):
            invoicing.apply_discount(invoice, bad)
    invoicing.apply_discount(invoice, 100)
    assert invoicing.invoice_total(invoice)["total_cents"] == 0
    with pytest.raises(ValueError):
        invoicing.apply_discount(issued(), 5)
