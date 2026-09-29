import uuid
from datetime import datetime, timedelta, timezone


def _stamp(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def _parse(text):
    return datetime.fromisoformat(text[:-1]).replace(tzinfo=timezone.utc)


def subscribe(customer, plan_cents, start):
    if not isinstance(plan_cents, int) or plan_cents <= 0:
        raise ValueError("plan_cents must be a positive int")
    return {"id": uuid.uuid4().hex, "customer": customer, "plan_cents": plan_cents,
            "started_at": _stamp(start), "renews_at": _stamp(start + timedelta(days=30))}


def renew(sub):
    return {**sub, "renews_at": _stamp(_parse(sub["renews_at"]) + timedelta(days=30)),
            "renewed_at": _stamp(datetime.now(timezone.utc))}


def monthly_revenue(subs):
    return sum(s["plan_cents"] for s in subs)
