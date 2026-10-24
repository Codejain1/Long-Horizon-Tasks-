"""Reports and exports."""

import csv

from ward import rules
from ward.doses import given_doses
from ward.store import EVENTS, PATIENTS


def daily_report(day):
    """Per drug given on the ward's calendar day: total micrograms and number of doses."""
    out = {}
    for e in given_doses():
        if rules.ward_day(e["_at"]) == day:
            row = out.setdefault(e["drug"], {"total_mcg": 0, "doses": 0})
            row["total_mcg"] += e["dose_mcg"]
            row["doses"] += 1
    return out


def export_csv(path):
    """Write every dose given, oldest first, for the pharmacy."""
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["event", "patient_mrn", "drug", "dose_mcg", "given_at", "nurse", "witness"])
        for e in EVENTS:
            if e["kind"] == "dose":
                w.writerow([e["id"], rules.mask_mrn(PATIENTS[e["patient"]]["mrn"]), e["drug"], e["dose_mcg"],
                            e["at"], e["nurse"], e["witness"] or ""])
