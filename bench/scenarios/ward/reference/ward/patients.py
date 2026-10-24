"""Patients."""

import uuid

from ward.errors import WardError
from ward.store import PATIENTS


def add_patient(name, mrn, weight_g, allergies=()):
    """Admit a patient: `mrn` is the hospital number, `weight_g` the weight in grams, `allergies` drug classes."""
    if not isinstance(weight_g, int) or isinstance(weight_g, bool) or weight_g <= 0:
        raise WardError("weight_g must be a positive whole number of grams")
    pid = uuid.uuid4().hex
    PATIENTS[pid] = {"id": pid, "name": name, "mrn": mrn, "weight_g": weight_g,
                     "allergies": [a.lower() for a in allergies]}
    return pid


def get_patient(patient_id):
    """The patient as a dict; raises WardError if unknown."""
    if patient_id not in PATIENTS:
        raise WardError(f"no patient {patient_id}")
    return dict(PATIENTS[patient_id], allergies=list(PATIENTS[patient_id]["allergies"]))
