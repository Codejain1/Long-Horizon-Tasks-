import json
import uuid
from datetime import datetime, timezone


def _stamp(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def _parse(text):
    return datetime.fromisoformat(text[:-1]).replace(tzinfo=timezone.utc)


def create_invoice(customer, amount_cents, due):
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive int")
    return {"id": uuid.uuid4().hex, "customer": customer, "amount_cents": amount_cents,
            "created_at": _stamp(datetime.now(timezone.utc)), "due": _stamp(due), "status": "open"}


def overdue(invoices, now):
    return [i for i in invoices if i["status"] == "open" and _parse(i["due"]) < now]


def to_json(invoice):
    return json.dumps(invoice)


def from_json(text):
    return json.loads(text)
