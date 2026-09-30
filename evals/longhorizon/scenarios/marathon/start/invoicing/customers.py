"""Customers."""

from __future__ import annotations

import uuid

from invoicing.clock import iso, now
from invoicing.errors import NotFound, ValidationError
from invoicing.store import data


def add_customer(name: str, email: str) -> dict:
    """Create a customer and return it."""
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("a customer needs a name")
    if not isinstance(email, str) or "@" not in email:
        raise ValidationError(f"not an email address: {email!r}")
    customer = {"id": uuid.uuid4().hex, "name": name.strip(), "email": email, "created_at": iso(now())}
    data()["customers"][customer["id"]] = customer
    return dict(customer)


def get_customer(customer_id: str) -> dict:
    """The customer with this id."""
    customer = data()["customers"].get(customer_id)
    if customer is None:
        raise NotFound(customer_id)
    return dict(customer)


def list_customers() -> list[dict]:
    """Every customer, oldest first."""
    return [dict(c) for c in sorted(data()["customers"].values(), key=lambda c: (c["created_at"], c["name"]))]
