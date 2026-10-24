"""The audit trail."""

import uuid

from ward.clock import now, stamp
from ward.errors import WardError
from ward.patients import get_patient
from ward.store import EVENTS


def log(patient_id, kind, **data):
    """Append an event about a patient and return it."""
    event = {"id": uuid.uuid4().hex, "patient": patient_id, "kind": kind, "at": stamp(now()), "_at": now(), **data}
    EVENTS.append(event)
    return event


def audit_log(patient_id):
    """Everything recorded about the patient, oldest first."""
    get_patient(patient_id)
    return [{k: v for k, v in e.items() if not k.startswith("_")} for e in EVENTS if e["patient"] == patient_id]


def record_egfr(patient_id, value):
    """Store a kidney-function reading taken now."""
    get_patient(patient_id)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise WardError("eGFR must be a positive whole number")
    log(patient_id, "egfr", value=value)


def latest_egfr(patient_id):
    """The latest eGFR reading, or None."""
    readings = [e["value"] for e in EVENTS if e["patient"] == patient_id and e["kind"] == "egfr"]
    return readings[-1] if readings else None


def update_weight(patient_id, weight_g):
    """Record a new weight in grams."""
    from ward.store import PATIENTS

    get_patient(patient_id)
    if not isinstance(weight_g, int) or isinstance(weight_g, bool) or weight_g <= 0:
        raise WardError("weight_g must be a positive whole number of grams")
    PATIENTS[patient_id]["weight_g"] = weight_g
    log(patient_id, "weight", weight_g=weight_g)
