"""Prescriptions."""

import uuid

from ward import rules
from ward.audit import log
from ward.clock import now, stamp
from ward.drugs import get_drug
from ward.errors import WardError
from ward.patients import get_patient
from ward.store import ORDERS

PUBLIC = ("id", "patient", "drug", "dose_mcg", "every_hours", "prescriber", "status", "created_at")


def _whole(x, low, high=None):
    return isinstance(x, int) and not isinstance(x, bool) and x >= low and (high is None or x <= high)


def prescribe(patient_id, drug_code, dose_mcg, every_hours, prescriber):
    """Create an active order; returns its id."""
    patient, drug = get_patient(patient_id), get_drug(drug_code)
    if not _whole(dose_mcg, 1) or not _whole(every_hours, 1, 24):
        raise WardError("dose_mcg must be positive whole micrograms, every_hours 1 to 24")
    if drug["drug_class"] in rules.blocked_classes(patient["allergies"]):
        raise WardError(f"patient is allergic to {drug['drug_class']} drugs")
    oid = uuid.uuid4().hex
    ORDERS[oid] = {"id": oid, "patient": patient_id, "drug": drug_code, "dose_mcg": dose_mcg,
                   "every_hours": every_hours, "prescriber": prescriber, "status": "active",
                   "created_at": stamp(now()), "_created": now()}
    log(patient_id, "order", order=oid, drug=drug_code, dose_mcg=dose_mcg)
    return oid


def order_record(order_id):
    """The stored order (internal)."""
    if order_id not in ORDERS:
        raise WardError(f"no order {order_id}")
    return ORDERS[order_id]


def get_order(order_id):
    """The order as a dict."""
    return {k: order_record(order_id)[k] for k in PUBLIC}


def orders_for(patient_id):
    """The patient's orders, oldest first."""
    return [get_order(o) for o, rec in ORDERS.items() if rec["patient"] == patient_id]


def stop_order(order_id):
    """Stop an order."""
    rec = order_record(order_id)
    rec["status"] = "stopped"
    log(rec["patient"], "stop", order=order_id)


def patient_drugs(patient_id):
    """The codes of the drugs in the patient's active orders, sorted."""
    return sorted({r["drug"] for r in ORDERS.values() if r["patient"] == patient_id and r["status"] == "active"})
