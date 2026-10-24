from datetime import date, datetime

from conftest import UTC, at, nurse, order, patient

import ward


def test_the_report_totals_each_drug():
    p, n = patient(), nurse()
    para, ibu = order(p, dose=1_000_000), order(p, code="IBU", dose=400_000)
    at(datetime(2026, 3, 10, 12, 0, tzinfo=UTC))
    ward.administer(para, n)
    ward.administer(ibu, n)
    at(datetime(2026, 3, 10, 18, 0, tzinfo=UTC))
    wrong = ward.administer(para, n)
    ward.correct_administration(wrong, "not given")
    assert ward.daily_report(date(2026, 3, 10)) == {"PARA": {"total_mcg": 1_000_000, "doses": 1},
                                                    "IBU": {"total_mcg": 400_000, "doses": 1}}


def test_days_are_london_days():
    """Rule from ticket 3: a day is the Europe/London calendar day (here, summer time)."""
    o, n = order(dose=1_000_000), nurse()
    at(datetime(2026, 6, 30, 23, 30, tzinfo=UTC))  # 00:30 on 1 July in London
    ward.administer(o, n)
    assert ward.daily_report(date(2026, 7, 1)) == {"PARA": {"total_mcg": 1_000_000, "doses": 1}}
    assert ward.daily_report(date(2026, 6, 30)) == {}
