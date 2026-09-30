"""The product catalogue."""

from __future__ import annotations

import uuid

from invoicing.errors import NotFound, ValidationError
from invoicing.money import is_cents
from invoicing.store import transaction


def add_product(name: str, price_cents: int, tax_bp: int = 1800) -> dict:
    """Add a product to the catalogue and return it. `tax_bp` is its GST rate in basis points (1800 = 18 %)."""
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("a product needs a name")
    if not is_cents(price_cents) or price_cents <= 0:
        raise ValidationError(f"price must be a positive number of cents: {price_cents!r}")
    if not is_cents(tax_bp) or not 0 <= tax_bp <= 10000:
        raise ValidationError(f"a GST rate is 0 to 10000 basis points: {tax_bp!r}")
    product = {"id": uuid.uuid4().hex, "name": name.strip(), "price_cents": price_cents, "tax_bp": tax_bp}
    with transaction() as db:
        db["products"][product["id"]] = product
    return dict(product)


def get_product(product_id: str) -> dict:
    """The product with this id."""
    with transaction() as db:
        product = db["products"].get(product_id)
        if product is None:
            raise NotFound(product_id)
        return dict(product)
