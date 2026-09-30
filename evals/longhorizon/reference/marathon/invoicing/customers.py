"""Customers."""

from __future__ import annotations

import uuid

from invoicing.clock import iso, now
from invoicing.errors import NotFound, ValidationError
from invoicing.invoices import _audit
from invoicing.store import transaction


def add_customer(name: str, email: str) -> dict:
    """Create a customer and return it."""
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("a customer needs a name")
    if not isinstance(email, str) or "@" not in email:
        raise ValidationError(f"not an email address: {email!r}")
    customer = {"id": uuid.uuid4().hex, "name": name.strip(), "email": email, "created_at": iso(now())}
    with transaction() as db:
        db["customers"][customer["id"]] = customer
    return dict(customer)


def get_customer(customer_id: str) -> dict:
    """The customer with this id."""
    with transaction() as db:
        customer = db["customers"].get(customer_id)
        if customer is None:
            raise NotFound(customer_id)
        return dict(customer)


def list_customers() -> list[dict]:
    """Every customer, oldest first."""
    with transaction() as db:
        return [dict(c) for c in sorted(db["customers"].values(), key=lambda c: (c["created_at"], c["name"]))]


def merge_customers(keep_id: str, remove_id: str) -> None:
    """Move `remove_id`'s invoices and schedules to `keep_id`, then delete `remove_id`."""
    if keep_id == remove_id:
        raise ValidationError("can't merge a customer into itself")
    with transaction() as db:
        for customer_id in (keep_id, remove_id):
            if customer_id not in db["customers"]:
                raise NotFound(customer_id)
        for invoice in db["invoices"].values():
            if invoice["customer_id"] == remove_id:
                invoice["customer_id"] = keep_id
                _audit(db, invoice["id"], "customer_merged")
        for schedule in db["schedules"].values():
            if schedule["customer_id"] == remove_id:
                schedule["customer_id"] = keep_id
        del db["customers"][remove_id]
