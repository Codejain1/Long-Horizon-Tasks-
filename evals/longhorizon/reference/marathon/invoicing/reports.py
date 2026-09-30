"""Statements, reports, reminders and late fees. Days and months are UTC."""

from __future__ import annotations

import re
from datetime import date

from invoicing.errors import NotFound, ValidationError
from invoicing.fx import _to_inr
from invoicing.invoices import OPEN, _audit, _balance, _line_tax, _totals, _utc_date
from invoicing.money import round_half_up
from invoicing.store import transaction

REMINDER_DAYS = (7, 14, 30)
AGING = (("current", 0), ("1-30", 30), ("31-60", 60), ("61-90", 90))


def customer_statement(customer_id: str, month: str) -> dict:
    """A customer's month (`"2026-03"`): opening balance, the invoices issued, payments and credit notes in it,
    oldest first, and the closing balance."""
    start, end = _month(month)
    with transaction() as db:
        if customer_id not in db["customers"]:
            raise NotFound(customer_id)
        invoices = {i["id"]: i for i in db["invoices"].values()
                    if i["customer_id"] == customer_id and i["status"] not in ("draft", "void")}
        entries = [{"type": "invoice", "id": i["id"], "at": i["issued_at"], "amount_cents": _totals(i)["total_cents"]}
                   for i in invoices.values()]
        entries += [{"type": "payment", "id": p["id"], "at": p["paid_at"], "amount_cents": p["amount_cents"]}
                    for p in db["payments"].values() if p["invoice_id"] in invoices]
        entries += [{"type": "credit", "id": c["id"], "at": c["created_at"], "amount_cents": c["amount_cents"]}
                    for c in db["credits"].values() if c["invoice_id"] in invoices]
        entries.sort(key=lambda e: e["at"])
        opening = sum(_signed(e) for e in entries if _utc_date(e["at"]) < start)
        during = [e for e in entries if start <= _utc_date(e["at"]) < end]
        return {"opening_cents": opening, "closing_cents": opening + sum(_signed(e) for e in during), "entries": during}


def gst_summary(month: str) -> dict:
    """GST charged on the invoices issued in `month` (not void), per rate in basis points, in INR cents."""
    start, end = _month(month)
    with transaction() as db:
        summary: dict[int, int] = {}
        for invoice in db["invoices"].values():
            if invoice["status"] in ("draft", "void") or not start <= _utc_date(invoice["issued_at"]) < end:
                continue
            per_rate: dict[int, int] = {}
            for line in invoice["lines"]:
                per_rate[line["tax_bp"]] = per_rate.get(line["tax_bp"], 0) + _line_tax(line)
            for rate, cents in per_rate.items():
                summary[rate] = summary.get(rate, 0) + _to_inr(db, invoice["currency"], cents)
        return summary


def aging_report(today: date) -> dict:
    """Per customer, what their unpaid invoices still owe in INR cents, by days past due."""
    with transaction() as db:
        report: dict[str, dict[str, int]] = {}
        for invoice in db["invoices"].values():
            balance = _balance(db, invoice) if invoice["status"] in OPEN else 0
            if balance <= 0:
                continue
            late = (today - date.fromisoformat(invoice["due_on"])).days
            bucket = next((name for name, upto in AGING if late <= upto), "90+")
            row = report.setdefault(invoice["customer_id"], {name: 0 for name, _ in AGING} | {"90+": 0})
            row[bucket] += _to_inr(db, invoice["currency"], balance)
        return report


def due_reminders(today: date) -> list[dict]:
    """Reminders for unpaid invoices 7, 14 and 30 days past due: each once, never repeated."""
    with transaction() as db:
        out = []
        for invoice in sorted((i for i in db["invoices"].values() if i["status"] in OPEN),
                              key=lambda i: (i["due_on"], i["created_at"])):
            late = (today - date.fromisoformat(invoice["due_on"])).days
            sent = db["reminders"].setdefault(invoice["id"], [])
            for days in REMINDER_DAYS:
                if late >= days and days not in sent:
                    sent.append(days)
                    out.append({"invoice_id": invoice["id"], "days": days})
        return out


def apply_late_fees(today: date) -> list[dict]:
    """1.5 % of the balance due for each full 30 days an unpaid invoice is past due, once per period."""
    with transaction() as db:
        added = []
        for invoice in sorted((i for i in db["invoices"].values() if i["status"] in OPEN),
                              key=lambda i: (i["due_on"], i["created_at"])):
            periods = (today - date.fromisoformat(invoice["due_on"])).days // 30
            for period in range(len(invoice["late_fees"]) + 1, periods + 1):
                cents = round_half_up(_balance(db, invoice) * 15, 1000)
                invoice["late_fees"].append({"period": period, "cents": cents})
                _audit(db, invoice["id"], "late_fee")
                added.append({"invoice_id": invoice["id"], "cents": cents})
        return added


def _month(month: str) -> tuple[date, date]:
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValidationError(f"a month looks like 2026-03: {month!r}")
    year, number = int(month[:4]), int(month[5:])
    return date(year, number, 1), date(year + number // 12, number % 12 + 1, 1)



def _signed(entry: dict) -> int:
    return entry["amount_cents"] if entry["type"] == "invoice" else -entry["amount_cents"]
