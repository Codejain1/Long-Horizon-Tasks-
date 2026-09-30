import invoicing
from conftest import customer, issued, pin


def test_a_monthly_statement_in_utc():
    c = customer()
    first = issued(10000, when="2026-02-20T10:00:00Z", customer_id=c)
    pin("2026-03-05T09:00:00Z")
    payment = invoicing.record_payment(first, 4000, "upi")
    second = issued(5000, when="2026-03-31T20:00:00Z", customer_id=c)  # 1 April in India, still March in UTC
    void = issued(700, when="2026-03-10T10:00:00Z", customer_id=c)
    invoicing.void_invoice(void, "mistake")
    pin("2026-04-01T10:00:00Z")
    credit = invoicing.credit_note(second, 1000, "discount")
    issued(999, when="2026-03-12T10:00:00Z")  # another customer's

    march = invoicing.customer_statement(c, "2026-03")
    assert (march["opening_cents"], march["closing_cents"]) == (10000, 11000)
    assert [(e["type"], e["id"], e["amount_cents"]) for e in march["entries"]] == [
        ("payment", payment["id"], 4000), ("invoice", second, 5000)]
    assert all(e["at"].endswith("Z") for e in march["entries"])
    april = invoicing.customer_statement(c, "2026-04")
    assert (april["opening_cents"], april["closing_cents"]) == (11000, 10000)
    assert [(e["type"], e["id"]) for e in april["entries"]] == [("credit", credit["id"])]
