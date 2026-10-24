from datetime import datetime

import pytest
from conftest import UTC, at, later, nurse, order, patient

import ward


def test_a_dose_over_the_daily_limit_is_refused():
    o, n = order(dose=1_000_000), nurse()
    for _ in range(4):
        ward.administer(o, n)
        later(hours=2)
    with pytest.raises(ward.WardError):
        ward.administer(o, n)


def test_the_limit_is_a_rolling_24_hours():
    """Rule from ticket 1: any rolling 24-hour period, not per calendar day."""
    o, n = order(dose=1_000_000, max_daily=2_000_000), nurse()
    at(datetime(2026, 3, 10, 20, 0, tzinfo=UTC))
    ward.administer(o, n)
    at(datetime(2026, 3, 11, 6, 0, tzinfo=UTC))
    ward.administer(o, n)
    at(datetime(2026, 3, 11, 10, 0, tzinfo=UTC))
    with pytest.raises(ward.WardError):
        ward.administer(o, n)
    at(datetime(2026, 3, 11, 20, 30, tzinfo=UTC))
    ward.administer(o, n)


def test_children_get_a_weight_scaled_limit():
    """Rule from ticket 1: under 50 kg, max_daily_mcg * weight_g // 50000."""
    o, n = order(patient(weight_g=25_000), dose=1_000_000), nurse()  # 4 g scaled to 2 g
    ward.administer(o, n)
    later(hours=6)
    ward.administer(o, n)
    later(hours=6)
    with pytest.raises(ward.WardError):
        ward.administer(o, n)
