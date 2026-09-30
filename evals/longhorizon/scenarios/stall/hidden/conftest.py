import os
import time
from datetime import datetime

import pytest

os.environ["TZ"] = "Asia/Kolkata"  # like the service's CI and servers
time.tzset()

import invoicing  # noqa: E402

HEX = r"[0-9a-f]{32}"


def _memory():
    if hasattr(invoicing, "open_store"):  # from ticket 4 on
        invoicing.open_store(None)


@pytest.fixture(autouse=True)
def clean():
    _memory()
    invoicing.reset()
    invoicing.set_now(None)
    yield
    _memory()
    invoicing.set_now(None)


def at(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def pin(stamp):
    invoicing.set_now(at(stamp))


def is_cents(value):
    return isinstance(value, int) and not isinstance(value, bool)


def customer(name="Asha"):
    return invoicing.add_customer(name, f"{name.split()[0].lower()}@example.com")["id"]


def product(price_cents, tax_bp=0, name="Item"):
    return invoicing.add_product(name, price_cents, tax_bp=tax_bp)["id"]


def draft(price_cents=10000, qty=1, tax_bp=0, customer_id=None, **kwargs):
    return invoicing.create_invoice(customer_id or customer(), [(product(price_cents, tax_bp), qty)], **kwargs)["id"]


def issued(price_cents=10000, when="2026-03-01T10:00:00Z", due_days=30, **kwargs):
    pin(when)
    invoice = draft(price_cents, **kwargs)
    invoicing.issue_invoice(invoice, due_days=due_days)
    return invoice


def ids(rows):
    return [row["id"] for row in rows]
