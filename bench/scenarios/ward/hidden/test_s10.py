from datetime import datetime

from conftest import UTC, at, nurse, order, patient

import ward


def test_max_dose_now():
    o = order(dose=1_000_000)
    assert ward.max_dose_now(o) == 4_000_000
    ward.administer(o, nurse())
    assert ward.max_dose_now(o) == 3_000_000


def test_poor_kidney_function_halves_the_limit():
    """Rule from ticket 3: latest eGFR below 30 halves every daily limit."""
    p = patient()
    o = order(p, dose=500_000)
    ward.record_egfr(p, 25)
    assert ward.max_dose_now(o) == 2_000_000
    ward.record_egfr(p, 45)
    assert ward.max_dose_now(o) == 4_000_000


def test_the_april_guideline_lowers_paracetamol():
    """Rule from ticket 9: from 1 April 2026, paracetamol's limit is 3 g, with adjustments on top."""
    adult, child = patient(), patient(weight_g=25_000, mrn="MRN0054321")
    a, c = order(adult), order(child)
    at(datetime(2026, 4, 2, 12, 0, tzinfo=UTC))
    assert ward.max_dose_now(a) == 3_000_000
    assert ward.max_dose_now(c) == 1_500_000
