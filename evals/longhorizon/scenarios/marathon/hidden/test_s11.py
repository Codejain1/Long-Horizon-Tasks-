from datetime import date

import invoicing
from conftest import issued, pin


def test_payments_report_in_inr_with_a_half_up_average():
    invoicing.set_fx_rate("USD", "80")
    inr = issued(10000, when="2026-03-01T10:00:00Z")
    pin("2026-03-01T11:00:00Z")
    invoicing.record_payment(inr, 100, "upi")
    pin("2026-03-31T20:00:00Z")  # 1 April in India, 31 March in UTC
    invoicing.record_payment(inr, 101, "upi")
    usd = issued(1000, when="2026-03-02T10:00:00Z", currency="USD")
    pin("2026-04-01T00:30:00Z")
    invoicing.record_payment(usd, 10, "card")  # 800 INR cents
    assert invoicing.payments_report(date(2026, 3, 1), date(2026, 3, 31)) == {
        "count": 2, "total_inr_cents": 201, "average_inr_cents": 101}
    assert invoicing.payments_report(date(2026, 4, 1), date(2026, 4, 30)) == {
        "count": 1, "total_inr_cents": 800, "average_inr_cents": 800}
    assert invoicing.payments_report(date(2026, 5, 1), date(2026, 5, 31)) == {
        "count": 0, "total_inr_cents": 0, "average_inr_cents": 0}
