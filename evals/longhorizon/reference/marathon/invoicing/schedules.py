"""Recurring invoices."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from invoicing.errors import NotFound, ValidationError
from invoicing.invoices import create_invoice, issue_invoice
from invoicing.money import is_cents
from invoicing.store import transaction


def create_schedule(customer_id: str, lines: list, every_days: int, start: date) -> dict:
    """Invoice `customer_id` for `lines` on `start` and every `every_days` days after it."""
    if not is_cents(every_days) or every_days <= 0:
        raise ValidationError(f"every_days must be a positive whole number: {every_days!r}")
    with transaction() as db:
        if customer_id not in db["customers"]:
            raise NotFound(customer_id)
        schedule = {"id": uuid.uuid4().hex, "customer_id": customer_id, "lines": [list(line) for line in lines],
                    "every_days": every_days, "start": start.isoformat(), "next": start.isoformat()}
        db["schedules"][schedule["id"]] = schedule
        return dict(schedule)


def run_schedules(today: date) -> list[dict]:
    """Create and issue every scheduled invoice due by `today` that doesn't exist yet, oldest first."""
    with transaction() as db:
        made = []
        for schedule in sorted(db["schedules"].values(), key=lambda s: (s["start"], s["id"])):
            while date.fromisoformat(schedule["next"]) <= today:
                invoice = create_invoice(schedule["customer_id"], [tuple(line) for line in schedule["lines"]])
                made.append((schedule["next"], issue_invoice(invoice["id"], 30)))
                schedule["next"] = (date.fromisoformat(schedule["next"])
                                    + timedelta(days=schedule["every_days"])).isoformat()
        return [invoice for _, invoice in sorted(made, key=lambda pair: pair[0])]
