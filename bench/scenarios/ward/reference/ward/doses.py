"""Giving doses: limits, witnesses, corrections, what's due."""

from datetime import timedelta

from ward import rules
from ward.audit import latest_egfr, log
from ward.clock import now, stamp
from ward.drugs import get_drug
from ward.errors import WardError
from ward.orders import order_record
from ward.patients import get_patient
from ward.store import EVENTS, NURSES, ORDERS


def _corrected_ids():
    return {e["corrects"] for e in EVENTS if e["kind"] == "correction"}


def _given(event, corrected):
    return event["kind"] == "dose" and not event.get("corrected") and event["id"] not in corrected


def daily_limit(patient_id, drug, at):
    """The patient's limit for the drug at `at`, in micrograms."""
    base = rules.guideline_base(drug["code"], drug["max_daily_mcg"], at)
    limit = rules.weight_scaled(base, get_patient(patient_id)["weight_g"])
    return rules.renal_scaled(limit, latest_egfr(patient_id))


def _received(patient_id, code, at, pending=()):
    since, corrected = rules.window_start(at), _corrected_ids()
    given = sum(e["dose_mcg"] for e in EVENTS if e["patient"] == patient_id and e.get("drug") == code
                and _given(e, corrected) and since < e["_at"] <= at)
    return given + sum(o["dose_mcg"] for o in pending if o["patient"] == patient_id and o["drug"] == code)


def _check(order_id, nurse_id, witness, at, pending=()):
    rec = order_record(order_id)
    if rec["status"] != "active":
        raise WardError("only active orders can be given")
    if nurse_id not in NURSES:
        raise WardError(f"no nurse {nurse_id}")
    drug = get_drug(rec["drug"])
    rules.check_witness(drug, nurse_id, witness, NURSES)
    if _received(rec["patient"], rec["drug"], at, pending) + rec["dose_mcg"] > daily_limit(rec["patient"], drug, at):
        raise WardError("this dose would go over the daily limit")
    return rec


def _record(rec, nurse_id, witness):
    return log(rec["patient"], "dose", order=rec["id"], drug=rec["drug"], dose_mcg=rec["dose_mcg"],
               nurse=nurse_id, witness=witness)["id"]


def administer(order_id, nurse_id, witness=None):
    """Record that the order's dose was given now; returns the event id."""
    return _record(_check(order_id, nurse_id, witness, now()), nurse_id, witness)


def administer_batch(items):
    """Give several doses at once, all or nothing; returns their event ids."""
    at, pending = now(), []
    for item in items:
        pending.append(_check(item["order"], item["nurse"], item.get("witness"), at, pending))
    return [_record(rec, item["nurse"], item.get("witness")) for rec, item in zip(pending, items)]


def administrations(order_id):
    """The order's doses, oldest first."""
    order_record(order_id)
    corrected = _corrected_ids()
    return [{"id": e["id"], "order": e["order"], "nurse": e["nurse"], "witness": e["witness"],
             "dose_mcg": e["dose_mcg"], "at": e["at"], "corrected": bool(e.get("corrected")) or e["id"] in corrected}
            for e in EVENTS if e["kind"] == "dose" and e["order"] == order_id]


def correct_administration(event_id, reason):
    """Record that a dose wasn't actually given."""
    event = next((e for e in EVENTS if e["id"] == event_id and e["kind"] == "dose"), None)
    if event is None:
        raise WardError(f"no dose {event_id}")
    if event.get("corrected") or event_id in _corrected_ids():
        raise WardError("already corrected")
    rules.record_correction(event, reason, log)


def given_doses():
    """Every dose given and not corrected, oldest first (internal)."""
    corrected = _corrected_ids()
    return [e for e in EVENTS if _given(e, corrected)]


def due_doses():
    """Each active order's next due time, earliest first."""
    out = []
    for rec in ORDERS.values():
        if rec["status"] != "active":
            continue
        last = [e["_at"] for e in given_doses() if e["order"] == rec["id"]]
        due = last[-1] + timedelta(hours=rec["every_hours"]) if last else rec["_created"]
        out.append((due, {"order": rec["id"], "patient": rec["patient"], "due_at": stamp(due)}))
    return [d for _, d in sorted(out, key=lambda x: x[0])]


def max_dose_now(order_id):
    """How many more micrograms of the order's drug the patient may receive now."""
    rec, at = order_record(order_id), now()
    return max(0, daily_limit(rec["patient"], get_drug(rec["drug"]), at) - _received(rec["patient"], rec["drug"], at))
