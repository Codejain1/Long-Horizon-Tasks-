"""The product catalogue."""

from __future__ import annotations

import uuid

from invoicing.errors import NotFound, ValidationError
from invoicing.money import is_cents
from invoicing.store import data


def add_product(name: str, price_cents: int) -> dict:
    """Add a product to the catalogue and return it."""
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("a product needs a name")
    if not is_cents(price_cents) or price_cents <= 0:
        raise ValidationError(f"price must be a positive number of cents: {price_cents!r}")
    product = {"id": uuid.uuid4().hex, "name": name.strip(), "price_cents": price_cents}
    data()["products"][product["id"]] = product
    return dict(product)


def get_product(product_id: str) -> dict:
    """The product with this id."""
    product = data()["products"].get(product_id)
    if product is None:
        raise NotFound(product_id)
    return dict(product)
