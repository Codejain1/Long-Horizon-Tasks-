import os
import time
from datetime import datetime, timedelta, timezone

import pytest

os.environ["TZ"] = "America/New_York"  # like the ward's CI: local-time code shows up as a 4-5 hour shift
time.tzset()

import ward  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 3, 10, 8, 0, tzinfo=UTC)  # London is on GMT until 29 March 2026


@pytest.fixture(autouse=True)
def clean():
    ward.reset()
    ward.set_now(T0)
    yield
    ward.set_now(None)


def at(when):
    ward.set_now(when)
    return when


def later(**delta):
    return at(ward.now() + timedelta(**delta))


def patient(weight_g=70_000, allergies=(), mrn="MRN0012345", name="Asha"):
    return ward.add_patient(name, mrn, weight_g, allergies)


def drug(code="PARA", cls="analgesic", max_daily=4_000_000, name=None):
    return ward.add_drug(code, name or code.title(), cls, max_daily)


def nurse(name="Ravi", badge=None):
    return ward.add_nurse(name, badge or name.upper())


def order(p=None, code="PARA", dose=1_000_000, every=6, **drug_kw):
    p = p or patient()
    if not _known(code):
        drug(code, **drug_kw)
    return ward.prescribe(p, code, dose, every, "Dr Rao")


def _known(code):
    try:
        ward.get_drug(code)
        return True
    except ward.WardError:
        return False


def stamp(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
