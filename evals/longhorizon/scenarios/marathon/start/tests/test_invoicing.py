import re
from datetime import datetime, timezone

import pytest

import invoicing


def test_customers_and_products():
    invoicing.set_now(datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc))
    c = invoicing.add_customer("Asha", "asha@example.com")
    assert re.fullmatch(r"[0-9a-f]{32}", c["id"]) and c["created_at"] == "2026-01-05T09:30:00Z"
    assert invoicing.get_customer(c["id"]) == c and invoicing.list_customers() == [c]
    p = invoicing.add_product("Tea", 250)
    assert invoicing.get_product(p["id"])["price_cents"] == 250
    with pytest.raises(ValueError):
        invoicing.add_product("Free", 0)
    with pytest.raises(KeyError):
        invoicing.get_customer("nope")


def test_draft_invoices_copy_prices():
    c = invoicing.add_customer("Asha", "asha@example.com")
    p = invoicing.add_product("Tea", 250)
    inv = invoicing.create_invoice(c["id"], [(p["id"], 3)])
    assert inv["status"] == "draft" and inv["subtotal_cents"] == 750
    assert invoicing.list_invoices(c["id"]) == [inv] and invoicing.list_invoices("other") == []
    with pytest.raises(ValueError):
        invoicing.create_invoice(c["id"], [])
    with pytest.raises(ValueError):
        invoicing.create_invoice(c["id"], [(p["id"], 0)])


def test_backups_round_trip(tmp_path):
    c = invoicing.add_customer("Asha", "asha@example.com")
    p = invoicing.add_product("Tea", 250)
    inv = invoicing.create_invoice(c["id"], [(p["id"], 2)])
    invoicing.export_backup(str(tmp_path / "b.json"))
    invoicing.reset()
    assert invoicing.list_customers() == []
    invoicing.import_backup(str(tmp_path / "b.json"))
    assert invoicing.get_invoice(inv["id"]) == inv and invoicing.get_customer(c["id"]) == c


def test_the_clock_is_utc():
    with pytest.raises(ValueError):
        invoicing.set_now(datetime(2026, 1, 1))
    assert invoicing.now().tzinfo is not None
