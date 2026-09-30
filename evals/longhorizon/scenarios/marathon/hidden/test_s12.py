import json

import invoicing
from conftest import customer, draft, issued, pin

CUSTOMER, PRODUCT, INVOICE = "c" * 32, "a" * 32, "b" * 32
V1 = {
    "version": 1,
    "customers": [{"id": CUSTOMER, "name": "Asha", "email": "asha@example.com", "created_at": "2026-01-01T00:00:00Z"}],
    "products": [{"id": PRODUCT, "name": "Tea", "price_cents": 250}],
    "invoices": [{"id": INVOICE, "customer": CUSTOMER, "status": "draft", "created_at": "2026-01-02T00:00:00Z",
                  "lines": [{"product": PRODUCT, "qty": 2, "unit_cents": 250}]}],
}


def test_version_1_backups_still_import(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps(V1))
    invoicing.import_backup(str(path))
    got = invoicing.get_invoice(INVOICE)
    assert (got["customer_id"], got["currency"], got["status"]) == (CUSTOMER, "INR", "draft")
    assert invoicing.invoice_total(INVOICE)["total_cents"] == 590  # the lines get the 18 % default rate
    invoicing.issue_invoice(INVOICE)
    assert invoicing.get_invoice(INVOICE)["number"] == "INV-0001"


def test_version_2_round_trips_the_whole_store(tmp_path):
    invoicing.set_fx_rate("USD", "80")
    c = customer()
    usd = issued(10000, when="2026-03-01T10:00:00Z", customer_id=c, currency="USD")
    pin("2026-03-02T10:00:00Z")
    invoicing.record_payment(usd, 1000, "upi")
    invoicing.credit_note(usd, 500, "damaged")
    discounted = draft(5000, customer_id=c)
    invoicing.apply_discount(discounted, 10)

    def state():
        return (invoicing.get_invoice(usd), invoicing.get_invoice(discounted), invoicing.balance_due(usd),
                invoicing.payments_for(usd), invoicing.total_in_inr(usd), invoicing.invoice_total(discounted))

    before = state()
    path = tmp_path / "backup.json"
    invoicing.export_backup(str(path))
    assert json.loads(path.read_text())["version"] == 2
    invoicing.reset()
    invoicing.import_backup(str(path))
    assert state() == before
    following = draft(100)
    invoicing.issue_invoice(following)
    assert invoicing.get_invoice(following)["number"] == "INV-0002"  # numbering carries on
