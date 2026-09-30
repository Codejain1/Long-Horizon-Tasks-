"""Invoices. A new invoice is a draft."""

from __future__ import annotations

import uuid

from invoicing.clock import iso, now
from invoicing.errors import NotFound, ValidationError
from invoicing.money import is_cents
from invoicing.store import data


def create_invoice(customer_id: str, lines: list) -> dict:
    """A draft invoice for `customer_id`; `lines` is a list of `(product_id, qty)`. Unit prices are copied
    from the catalogue, so later price changes don't touch existing invoices."""
    tables = data()
    if customer_id not in tables["customers"]:
        raise NotFound(customer_id)
    if not lines:
        raise ValidationError("an invoice needs at least one line")
    rows = []
    for product_id, qty in lines:
        product = tables["products"].get(product_id)
        if product is None:
            raise NotFound(product_id)
        if not is_cents(qty) or qty <= 0:
            raise ValidationError(f"quantity must be a positive whole number: {qty!r}")
        rows.append({"product_id": product_id, "qty": qty, "unit_cents": product["price_cents"]})
    invoice = {"id": uuid.uuid4().hex, "customer_id": customer_id, "status": "draft", "created_at": iso(now()),
               "lines": rows}
    tables["invoices"][invoice["id"]] = invoice
    return _view(invoice)


def get_invoice(invoice_id: str) -> dict:
    """The invoice with this id, with its `subtotal_cents`."""
    invoice = data()["invoices"].get(invoice_id)
    if invoice is None:
        raise NotFound(invoice_id)
    return _view(invoice)


def list_invoices(customer_id: str | None = None) -> list[dict]:
    """Invoices, oldest first; only one customer's if `customer_id` is given."""
    rows = [i for i in data()["invoices"].values() if customer_id in (None, i["customer_id"])]
    return [_view(i) for i in sorted(rows, key=lambda i: i["created_at"])]


def _view(invoice: dict) -> dict:
    view = dict(invoice, lines=[dict(line) for line in invoice["lines"]])
    view["subtotal_cents"] = sum(line["qty"] * line["unit_cents"] for line in invoice["lines"])
    return view
