"""The committee's clinical, legal and operational rules (tickets 1-3 and 9), in one place."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from ward.errors import WardError

LONDON = ZoneInfo("Europe/London")
CROSS_REACTIVE = {"penicillin": {"cephalosporin"}, "cephalosporin": {"penicillin"}}
PARA_GUIDELINE = (date(2026, 4, 1), 3_000_000)


def blocked_classes(allergies):
    """The drug classes a patient with these allergies must not get."""
    blocked = set(allergies)
    for a in allergies:
        blocked |= CROSS_REACTIVE.get(a, set())
    return blocked


def window_start(at):
    """Doses after this time count towards the limit at `at`: any rolling 24 hours."""
    return at - timedelta(hours=24)


def guideline_base(code, base, at):
    """The national guideline overrides paracetamol's catalogue limit from 1 April 2026 (London)."""
    start, limit = PARA_GUIDELINE
    return limit if code == "PARA" and ward_day(at) >= start else base


def weight_scaled(limit, weight_g):
    """Under 50 kg, the limit scales with weight."""
    return limit * weight_g // 50_000 if weight_g < 50_000 else limit


def renal_scaled(limit, egfr):
    """A latest eGFR below 30 halves the limit."""
    return limit // 2 if egfr is not None and egfr < 30 else limit


def check_witness(drug, nurse_id, witness, nurses):
    """Opioids need a second, registered nurse as witness."""
    if drug["drug_class"] == "opioid" and (witness is None or witness == nurse_id or witness not in nurses):
        raise WardError("controlled drugs need another registered nurse as witness")


def record_correction(event, reason, log):
    """The audit trail is append-only: a correction is a new event pointing at the original."""
    log(event["patient"], "correction", corrects=event["id"], reason=reason)


def ward_day(at):
    """The ward's calendar day: London's."""
    return at.astimezone(LONDON).date()


def mask_mrn(mrn):
    """Only the MRN's last 3 characters leave WardSafe."""
    return "*" * max(0, len(mrn) - 3) + mrn[-3:]
