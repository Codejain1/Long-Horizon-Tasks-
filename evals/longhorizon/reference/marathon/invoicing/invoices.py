"""Invoices: draft, issued, partially paid, paid, void or written off."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from invoicing.clock import iso, now
from invoicing.errors import Conflict, NotFound, ValidationError
from invoicing.money import is_cents, round_half_up
from invoicing.store import transaction

OPEN = ("issued", "partially_paid")  # still owed: can be paid, credited, charged late fees, written off
SETTLING = ("issued", "partially_paid", "paid")


def create_invoice(customer_id: str, lines: list, currency: str = "INR") -> dict:
    """A draft invoice for `customer_id`; `lines` is a list of `(product_id, qty)`. Unit prices and GST rates
    are copied from the catalogue; amounts are cents of `currency`."""
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        raise ValidationError(f"a currency is a 3-letter code: {currency!r}")
    with transaction() as db:
        if customer_id not in db["customers"]:
            raise NotFound(customer_id)
        if not lines:
            raise ValidationError("an invoice needs at least one line")
        rows = []
        for product_id, qty in lines:
            product = db["products"].get(product_id)
            if product is None:
                raise NotFound(product_id)
            if not is_cents(qty) or qty <= 0:
                raise ValidationError(f"quantity must be a positive whole number: {qty!r}")
            rows.append({"product_id": product_id, "qty": qty, "unit_cents": product["price_cents"],
                         "tax_bp": product["tax_bp"], "discount_pct": 0})
        invoice = {"id": uuid.uuid4().hex, "number": None, "customer_id": customer_id, "currency": currency,
                   "status": "draft", "created_at": iso(now()), "issued_at": None, "due_on": None, "lines": rows,
                   "late_fees": [], "written_off_cents": 0}
        db["invoices"][invoice["id"]] = invoice
        _audit(db, invoice["id"], "created")
        return _view(invoice)


def get_invoice(invoice_id: str) -> dict:
    """The invoice with this id, with its `subtotal_cents`."""
    with transaction() as db:
        return _view(_get(db, invoice_id))


def list_invoices(customer_id: str | None = None) -> list[dict]:
    """Invoices, oldest first; only one customer's if `customer_id` is given."""
    with transaction() as db:
        rows = [i for i in db["invoices"].values() if customer_id in (None, i["customer_id"])]
        return [_view(i) for i in sorted(rows, key=lambda i: i["created_at"])]


def search_invoices(customer_id: str | None = None, status: str | None = None,
                    min_total_cents: int | None = None) -> list[dict]:
    """Invoices matching every filter given, newest first."""
    with transaction() as db:
        rows = [i for i in db["invoices"].values()
                if customer_id in (None, i["customer_id"]) and status in (None, i["status"])
                and (min_total_cents is None or _totals(i)["total_cents"] >= min_total_cents)]
        return [_view(i) for i in sorted(rows, key=lambda i: i["created_at"], reverse=True)]


def invoice_total(invoice_id: str) -> dict:
    """`subtotal_cents` (after discounts), `tax_cents` (GST per line, half up), `late_fees_cents` and
    `total_cents`."""
    with transaction() as db:
        return _totals(_get(db, invoice_id))


def balance_due(invoice_id: str) -> int:
    """What's still owed: the total less payments (net of refunds), credit notes and any write-off."""
    with transaction() as db:
        return _balance(db, _get(db, invoice_id))


def apply_discount(invoice_id: str, percent: int) -> dict:
    """Take a whole-number percentage off every line of a draft, before tax, half up per line."""
    if not is_cents(percent) or not 0 <= percent <= 100:
        raise ValidationError(f"a discount is a whole percentage from 0 to 100: {percent!r}")
    with transaction() as db:
        invoice = _get(db, invoice_id)
        if invoice["status"] != "draft":
            raise Conflict(f"only drafts can be discounted: {invoice['status']}")
        for line in invoice["lines"]:
            line["discount_pct"] = percent
        _audit(db, invoice_id, "discounted")
        return _view(invoice)


def issue_invoice(invoice_id: str, due_days: int = 30) -> dict:
    """Issue a draft: the next number (INV-0001, …), `issued_at` now and `due_on` the UTC date `due_days` later."""
    if not is_cents(due_days) or due_days < 0:
        raise ValidationError(f"due_days must be a whole number of days: {due_days!r}")
    with transaction() as db:
        invoice = _get(db, invoice_id)
        if invoice["status"] != "draft":
            raise Conflict(f"only drafts can be issued: {invoice['status']}")
        number = db["counters"].get("invoice", 0) + 1
        db["counters"]["invoice"] = number
        issued = now()
        invoice.update(number=f"INV-{number:04d}", status="issued", issued_at=iso(issued),
                       due_on=(issued.astimezone(timezone.utc).date() + timedelta(days=due_days)).isoformat())
        _audit(db, invoice_id, "issued")
        return _view(invoice)


def void_invoice(invoice_id: str, reason: str) -> dict:
    """Void an issued invoice that has no payments."""
    with transaction() as db:
        invoice = _get(db, invoice_id)
        if invoice["status"] != "issued" or any(p["invoice_id"] == invoice_id for p in db["payments"].values()):
            raise Conflict("only an issued invoice with no payments can be voided")
        invoice.update(status="void", void_reason=reason, voided_at=iso(now()))
        _audit(db, invoice_id, "voided")
        return _view(invoice)


def write_off(invoice_id: str, reason: str) -> dict:
    """Write off what's still owed on an issued or partially paid invoice."""
    with transaction() as db:
        invoice = _get(db, invoice_id)
        if invoice["status"] not in OPEN:
            raise Conflict(f"only an unpaid invoice can be written off: {invoice['status']}")
        invoice["written_off_cents"] = _balance(db, invoice)
        invoice.update(status="written_off", write_off_reason=reason)
        _audit(db, invoice_id, "written_off")
        return _view(invoice)


def overdue_invoices(today: date) -> list[dict]:
    """Unpaid invoices due before `today` (a UTC day), oldest due date first."""
    with transaction() as db:
        rows = [i for i in db["invoices"].values() if i["status"] in OPEN and date.fromisoformat(i["due_on"]) < today]
        return [_view(i) for i in sorted(rows, key=lambda i: (i["due_on"], i["created_at"]))]


def audit_log(invoice_id: str) -> list[dict]:
    """Every change to the invoice, oldest first: `{"at", "action"}`."""
    with transaction() as db:
        _get(db, invoice_id)
        return [dict(entry) for entry in db["audit"].get(invoice_id, [])]


def _get(db: dict, invoice_id: str) -> dict:
    invoice = db["invoices"].get(invoice_id)
    if invoice is None:
        raise NotFound(invoice_id)
    return invoice


def _view(invoice: dict) -> dict:
    view = {k: v for k, v in invoice.items() if k not in ("late_fees", "written_off_cents")}
    view["lines"] = [dict(line) for line in invoice["lines"]]
    view["subtotal_cents"] = _totals(invoice)["subtotal_cents"]
    return view


def _line_net(line: dict) -> int:
    gross = line["qty"] * line["unit_cents"]
    return gross - round_half_up(gross * line["discount_pct"], 100)


def _line_tax(line: dict) -> int:
    return round_half_up(_line_net(line) * line["tax_bp"], 10000)


def _totals(invoice: dict) -> dict:
    subtotal = sum(_line_net(line) for line in invoice["lines"])
    tax = sum(_line_tax(line) for line in invoice["lines"])
    fees = sum(fee["cents"] for fee in invoice["late_fees"])
    return {"subtotal_cents": subtotal, "tax_cents": tax, "late_fees_cents": fees, "total_cents": subtotal + tax + fees}


def _paid(db: dict, invoice_id: str) -> int:
    payments = {p["id"]: p["amount_cents"] for p in db["payments"].values() if p["invoice_id"] == invoice_id}
    refunded = sum(r["amount_cents"] for r in db["refunds"].values() if r["payment_id"] in payments)
    return sum(payments.values()) - refunded


def _credited(db: dict, invoice_id: str) -> int:
    return sum(c["amount_cents"] for c in db["credits"].values() if c["invoice_id"] == invoice_id)


def _balance(db: dict, invoice: dict) -> int:
    return (_totals(invoice)["total_cents"] - _paid(db, invoice["id"]) - _credited(db, invoice["id"])
            - invoice["written_off_cents"])


def _settle(db: dict, invoice: dict) -> None:
    """Paid once nothing is owed; partially paid once anything was paid or credited."""
    if invoice["status"] in SETTLING:
        settled = _paid(db, invoice["id"]) + _credited(db, invoice["id"])
        invoice["status"] = "paid" if _balance(db, invoice) == 0 else "partially_paid" if settled else "issued"


def _audit(db: dict, invoice_id: str, action: str) -> None:
    db["audit"].setdefault(invoice_id, []).append({"at": iso(now()), "action": action})


def _utc_date(stamp: str) -> date:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).date()
