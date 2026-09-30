"""Payments, credit notes and refunds against issued invoices."""

from __future__ import annotations

import uuid
from datetime import date

from invoicing.clock import iso, now
from invoicing.errors import Conflict, NotFound, ValidationError
from invoicing.fx import _to_inr
from invoicing.invoices import OPEN, _audit, _balance, _get, _settle, _utc_date
from invoicing.money import is_cents, round_half_up
from invoicing.store import transaction


def record_payment(invoice_id: str, amount_cents: int, method: str) -> dict:
    """Record a payment against an unpaid issued invoice and return it."""
    _check_amount(amount_cents)
    with transaction() as db:
        invoice = _get(db, invoice_id)
        if invoice["status"] not in OPEN:
            raise Conflict(f"can't pay an invoice that is {invoice['status']}")
        if amount_cents > _balance(db, invoice):
            raise ValidationError("the payment is more than the amount due")
        payment = {"id": uuid.uuid4().hex, "invoice_id": invoice_id, "amount_cents": amount_cents, "method": method,
                   "paid_at": iso(now())}
        db["payments"][payment["id"]] = payment
        _settle(db, invoice)
        _audit(db, invoice_id, "payment")
        return dict(payment)


def payments_for(invoice_id: str) -> list[dict]:
    """The invoice's payments, oldest first."""
    with transaction() as db:
        _get(db, invoice_id)
        rows = [p for p in db["payments"].values() if p["invoice_id"] == invoice_id]
        return [dict(p) for p in sorted(rows, key=lambda p: p["paid_at"])]


def credit_note(invoice_id: str, amount_cents: int, reason: str) -> dict:
    """Credit part of what's owed on an unpaid issued invoice and return the credit note."""
    _check_amount(amount_cents)
    with transaction() as db:
        invoice = _get(db, invoice_id)
        if invoice["status"] not in OPEN:
            raise Conflict(f"can't credit an invoice that is {invoice['status']}")
        if amount_cents > _balance(db, invoice):
            raise ValidationError("the credit is more than the balance due")
        credit = {"id": uuid.uuid4().hex, "invoice_id": invoice_id, "amount_cents": amount_cents, "reason": reason,
                  "created_at": iso(now())}
        db["credits"][credit["id"]] = credit
        _settle(db, invoice)
        _audit(db, invoice_id, "credit")
        return dict(credit)


def refund_payment(payment_id: str, amount_cents: int, reason: str) -> dict:
    """Refund part of a payment and return the refund; what's owed goes back up."""
    _check_amount(amount_cents)
    with transaction() as db:
        payment = db["payments"].get(payment_id)
        if payment is None:
            raise NotFound(payment_id)
        left = payment["amount_cents"] - sum(r["amount_cents"] for r in db["refunds"].values()
                                             if r["payment_id"] == payment_id)
        if amount_cents > left:
            raise ValidationError("the refund is more than what's left of the payment")
        refund = {"id": uuid.uuid4().hex, "payment_id": payment_id, "amount_cents": amount_cents, "reason": reason,
                  "created_at": iso(now())}
        db["refunds"][refund["id"]] = refund
        _settle(db, db["invoices"][payment["invoice_id"]])
        _audit(db, payment["invoice_id"], "refund")
        return dict(refund)


def refunds_for(invoice_id: str) -> list[dict]:
    """Refunds of the invoice's payments, oldest first."""
    with transaction() as db:
        _get(db, invoice_id)
        payments = {p["id"] for p in db["payments"].values() if p["invoice_id"] == invoice_id}
        rows = [r for r in db["refunds"].values() if r["payment_id"] in payments]
        return [dict(r) for r in sorted(rows, key=lambda r: r["created_at"])]


def payments_report(start: date, end: date) -> dict:
    """Payments made from `start` to `end` (UTC days, inclusive): count, total and average in INR cents."""
    with transaction() as db:
        amounts = [_to_inr(db, db["invoices"][p["invoice_id"]]["currency"], p["amount_cents"])
                   for p in db["payments"].values() if start <= _utc_date(p["paid_at"]) <= end]
        total = sum(amounts)
        return {"count": len(amounts), "total_inr_cents": total,
                "average_inr_cents": round_half_up(total, len(amounts)) if amounts else 0}


def _check_amount(amount_cents: int) -> None:
    if not is_cents(amount_cents) or amount_cents <= 0:
        raise ValidationError(f"an amount is a positive number of cents: {amount_cents!r}")
