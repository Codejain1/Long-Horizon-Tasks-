import invoicing
from conftest import customer, is_cents


def test_gst_is_charged_per_line_and_rounded_half_up():
    tea = invoicing.add_product("Tea", 333)  # default 18 %: 59.94 -> 60
    pen = invoicing.add_product("Pen", 25, tax_bp=1000)  # 2.5 -> 3, half up
    invoice = invoicing.create_invoice(customer(), [(tea["id"], 1), (pen["id"], 1)])
    total = invoicing.invoice_total(invoice["id"])
    assert (total["subtotal_cents"], total["tax_cents"], total["total_cents"]) == (358, 63, 421)
    assert all(is_cents(total[k]) for k in ("subtotal_cents", "tax_cents", "total_cents"))


def test_tax_is_per_line_not_on_the_total():
    a = invoicing.add_product("A", 5, tax_bp=1000)
    b = invoicing.add_product("B", 5, tax_bp=1000)
    total = invoicing.invoice_total(invoicing.create_invoice(customer(), [(a["id"], 1), (b["id"], 1)])["id"])
    assert (total["tax_cents"], total["total_cents"]) == (2, 12)


def test_quantities_and_the_default_rate():
    chai = invoicing.add_product("Chai", 1000)
    total = invoicing.invoice_total(invoicing.create_invoice(customer(), [(chai["id"], 3)])["id"])
    assert (total["subtotal_cents"], total["tax_cents"], total["total_cents"]) == (3000, 540, 3540)
