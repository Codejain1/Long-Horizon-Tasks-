from datetime import date

import invoicing
from conftest import at, draft, issued, pin


def test_the_audit_log_records_every_change_in_order():
    pin("2026-03-01T09:00:00Z")
    invoice = draft(10000)
    pin("2026-03-01T09:05:00Z")
    invoicing.apply_discount(invoice, 10)
    pin("2026-03-01T10:00:00Z")
    invoicing.issue_invoice(invoice)
    pin("2026-03-02T10:00:00Z")
    payment = invoicing.record_payment(invoice, 4000, "upi")
    pin("2026-03-03T10:00:00Z")
    invoicing.credit_note(invoice, 1000, "damaged")
    pin("2026-03-04T10:00:00Z")
    invoicing.refund_payment(payment["id"], 500, "returned")
    log = invoicing.audit_log(invoice)
    assert [e["action"] for e in log] == ["created", "discounted", "issued", "payment", "credit", "refund"]
    assert all(e["at"].endswith("Z") for e in log)
    assert [at(e["at"]) for e in log] == [at(s) for s in (
        "2026-03-01T09:00:00Z", "2026-03-01T09:05:00Z", "2026-03-01T10:00:00Z", "2026-03-02T10:00:00Z",
        "2026-03-03T10:00:00Z", "2026-03-04T10:00:00Z")]


def test_voids_and_late_fees_are_logged():
    invoice = issued(10000, when="2026-01-01T10:00:00Z", due_days=0)
    pin("2026-02-01T10:00:00Z")
    invoicing.apply_late_fees(date(2026, 2, 1))
    assert [e["action"] for e in invoicing.audit_log(invoice)] == ["created", "issued", "late_fee"]
    void = issued(500, when="2026-03-01T10:00:00Z")
    invoicing.void_invoice(void, "duplicate")
    assert [e["action"] for e in invoicing.audit_log(void)] == ["created", "issued", "voided"]
