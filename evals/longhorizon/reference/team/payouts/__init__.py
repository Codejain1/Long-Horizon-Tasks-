import uuid
from datetime import datetime, timezone


def _stamp(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def _parse(text):
    return datetime.fromisoformat(text[:-1]).replace(tzinfo=timezone.utc)


def schedule_payout(merchant, amount_cents, when):
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive int")
    return {"id": uuid.uuid4().hex, "merchant": merchant, "amount_cents": amount_cents,
            "scheduled_for": _stamp(when), "created_at": _stamp(datetime.now(timezone.utc))}


def due_payouts(payouts, now):
    return [p for p in payouts if _parse(p["scheduled_for"]) <= now]


def split(amount_cents, shares):
    if not shares or any(s < 0 for s in shares) or sum(shares) == 0:
        raise ValueError("shares must be non-negative with a positive total")
    parts = [amount_cents * s // sum(shares) for s in shares]
    for i in range(amount_cents - sum(parts)):
        parts[i % len(parts)] += 1
    return parts
