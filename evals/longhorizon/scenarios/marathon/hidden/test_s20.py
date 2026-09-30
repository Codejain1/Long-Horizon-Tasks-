import invoicing
from conftest import customer, pin, product


def issue(c, lines, when, **kwargs):
    pin(when)
    invoice = invoicing.create_invoice(c, lines, **kwargs)["id"]
    invoicing.issue_invoice(invoice)
    return invoice


def test_gst_per_rate_for_the_utc_month():
    invoicing.set_fx_rate("USD", "80")
    c = customer()
    issue(c, [(product(1000, 1800), 1), (product(1000, 500), 2)], "2026-03-05T10:00:00Z")  # 180 at 18 %, 100 at 5 %
    issue(c, [(product(100, 1800), 1)], "2026-03-31T20:00:00Z", currency="USD")  # 18 US cents = 1440; 1 April in India
    void = issue(c, [(product(5000, 1800), 1)], "2026-03-10T10:00:00Z")
    invoicing.void_invoice(void, "mistake")
    issue(c, [(product(1000, 1800), 1)], "2026-04-01T10:00:00Z")
    pin("2026-03-11T10:00:00Z")
    invoicing.create_invoice(c, [(product(9999, 1800), 1)])  # a draft isn't issued
    assert invoicing.gst_summary("2026-03") == {1800: 180 + 1440, 500: 100}
    assert invoicing.gst_summary("2026-04") == {1800: 180}
