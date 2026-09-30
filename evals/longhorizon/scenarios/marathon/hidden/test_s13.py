import invoicing
from conftest import customer, draft, ids, issued, pin


def test_search_filters_combine_newest_first():
    asha, ravi = customer("Asha"), customer("Ravi")
    one = issued(5000, when="2026-03-01T10:00:00Z", customer_id=asha)
    two = issued(20000, when="2026-03-02T10:00:00Z", customer_id=asha)
    pin("2026-03-03T10:00:00Z")
    three = draft(30000, customer_id=asha)
    four = issued(40000, when="2026-03-04T10:00:00Z", customer_id=ravi)
    assert ids(invoicing.search_invoices(customer_id=asha)) == [three, two, one]
    assert ids(invoicing.search_invoices(status="issued")) == [four, two, one]
    assert ids(invoicing.search_invoices(customer_id=asha, min_total_cents=20000)) == [three, two]
    assert ids(invoicing.search_invoices(customer_id=asha, status="issued", min_total_cents=10000)) == [two]
    assert ids(invoicing.search_invoices()) == [four, three, two, one]
