"""The drug catalogue."""

from ward.errors import WardError
from ward.store import DRUGS


def add_drug(code, name, drug_class, max_daily_mcg):
    """Add a drug: `drug_class` like "penicillin" or "opioid", `max_daily_mcg` its daily limit in micrograms."""
    if not isinstance(max_daily_mcg, int) or isinstance(max_daily_mcg, bool) or max_daily_mcg <= 0:
        raise WardError("max_daily_mcg must be a positive whole number")
    DRUGS[code] = {"code": code, "name": name, "drug_class": drug_class.lower(), "max_daily_mcg": max_daily_mcg}
    return code


def get_drug(code):
    """The drug as a dict; raises WardError if unknown."""
    if code not in DRUGS:
        raise WardError(f"no drug {code}")
    return dict(DRUGS[code])
