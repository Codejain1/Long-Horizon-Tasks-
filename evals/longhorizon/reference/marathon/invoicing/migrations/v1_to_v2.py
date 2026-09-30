"""Backup format 1 to 2: invoices gain a currency, numbers, dates, GST rates and discounts."""

from __future__ import annotations

DEFAULT_TAX_BP = 1800


def upgrade(backup: dict) -> dict:
    """A version-1 backup's records as version-2 tables."""
    return {
        "customers": {c["id"]: dict(c) for c in backup.get("customers", [])},
        "products": {p["id"]: {**p, "tax_bp": DEFAULT_TAX_BP} for p in backup.get("products", [])},
        "invoices": {i["id"]: {
            "id": i["id"], "number": None, "customer_id": i["customer"], "currency": "INR", "status": "draft",
            "created_at": i["created_at"], "issued_at": None, "due_on": None, "late_fees": [], "written_off_cents": 0,
            "lines": [{"product_id": line["product"], "qty": line["qty"], "unit_cents": line["unit_cents"],
                       "tax_bp": DEFAULT_TAX_BP, "discount_pct": 0} for line in i["lines"]],
        } for i in backup.get("invoices", [])},
    }
