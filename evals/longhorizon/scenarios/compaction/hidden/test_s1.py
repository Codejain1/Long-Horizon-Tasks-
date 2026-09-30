"""Acceptance tests for all 8 parts. Parts 4-8 come after the compaction; several also fail when the agent takes
the part's shortcut (a float average, local days, float rates) rather than keeping the session-1 constraints."""

import csv
import os
import time
from datetime import date, datetime, timedelta, timezone

import pytest

import tab

IST = timezone(timedelta(hours=5, minutes=30))


def at(*args, tz=timezone.utc):
    return datetime(*args, tzinfo=tz)


def group(*members):
    return tab.new_group("trip", list(members or ("A", "B", "C")))


def test_part1_rejects_bad_expenses():
    g = group()
    with pytest.raises(ValueError):
        tab.add_expense(g, "A", 100, ["A", "B"], datetime(2026, 3, 1, 12))  # naive
    with pytest.raises(ValueError):
        tab.add_expense(g, "Z", 100, ["A"], at(2026, 3, 1, 12))
    with pytest.raises(ValueError):
        tab.add_expense(g, "A", 100, ["A", "Z"], at(2026, 3, 1, 12))
    with pytest.raises(ValueError):
        tab.add_expense(g, "A", 0, ["A"], at(2026, 3, 1, 12))


def test_part2_even_split_with_leftover_cents_to_the_first_participants():
    g = group()
    tab.add_expense(g, "A", 100, ["A", "B", "C"], at(2026, 3, 1, 12))
    tab.add_expense(g, "C", 7, ["B", "C"], at(2026, 3, 1, 13))
    result = tab.balances(g)
    assert list(result) == ["A", "B", "C"]
    assert result == {"A": 66, "B": -37, "C": -29}
    assert all(type(v) is int for v in result.values())


def test_part3_largest_debtor_pays_largest_creditor():
    g = group("A", "B", "C", "D")
    tab.add_expense(g, "A", 50, ["C"], at(2026, 3, 1, 12))
    tab.add_expense(g, "B", 20, ["D"], at(2026, 3, 1, 12))
    tab.add_expense(g, "B", 10, ["C"], at(2026, 3, 1, 12))
    assert [tuple(t) for t in tab.settle_up(g)] == [("C", "A", 50), ("D", "B", 20), ("C", "B", 10)]


def test_part3_ties_go_to_the_member_listed_first():
    for members, first in ((("A", "B", "C"), "B"), (("C", "B", "A"), "C")):
        g = group(*members)
        tab.add_expense(g, "A", 100, ["A", "B", "C"], at(2026, 3, 1, 12))  # B and C both owe 33
        second = "C" if first == "B" else "B"
        assert [tuple(t) for t in tab.settle_up(g)] == [(first, "A", 33), (second, "A", 33)], members


def test_part4_average_rounds_half_up_in_cents():
    g = group()
    assert tab.average_expense(g) == 0
    tab.add_expense(g, "A", 100, ["A"], at(2026, 3, 1, 12))
    tab.add_expense(g, "B", 101, ["B"], at(2026, 3, 1, 12))
    assert tab.average_expense(g) == 101 and type(tab.average_expense(g)) is int  # round(100.5) would be 100
    tab.add_expense(g, "C", 1, ["C"], at(2026, 3, 1, 12))
    assert tab.average_expense(g) == 67


def test_part5_csv_has_utc_stamps(tmp_path):
    g = group()
    tab.add_expense(g, "A", 100, ["A", "B"], at(2026, 4, 1, 1, 30, tz=IST))  # 2026-03-31 20:00 UTC
    path = tmp_path / "out.csv"
    tab.export_csv(g, str(path))
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    assert rows == [["payer", "amount_cents", "participants", "at"], ["A", "100", "A;B", "2026-03-31T20:00:00Z"]]


def test_part6_days_are_utc_days():
    g = group()
    tab.add_expense(g, "A", 100, ["A"], at(2026, 4, 1, 1, 30, tz=IST))  # March 31 in UTC, April 1 in India
    tab.add_expense(g, "B", 200, ["B"], at(2026, 4, 1, 10))
    counts = [len(tab.expenses_on(g, date(2026, 3, d))) for d in (30, 31)] + \
        [len(tab.expenses_on(g, date(2026, 4, d))) for d in (1, 2)]
    assert counts == [0, 1, 1, 0]


def test_part6_today_is_the_utc_date_whatever_the_machine_zone():
    g = group()
    tab.add_expense(g, "A", 100, ["A"], datetime.now(timezone.utc))
    saved = os.environ.get("TZ")
    try:
        for zone in ("Pacific/Kiritimati", "Etc/GMT+12"):  # UTC+14 and UTC-12: one is on another date right now
            os.environ["TZ"] = zone
            time.tzset()
            assert len(tab.expenses_on(g)) == 1, zone
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


def test_part7_net_balances_and_balances_agree():
    g = group()
    tab.add_expense(g, "A", 100, ["A", "B", "C"], at(2026, 3, 1, 12))
    assert tab.net_balances(g) == tab.balances(g) == {"A": 66, "B": -33, "C": -33}


def test_part8_converts_at_a_string_rate_rounding_half_up():
    assert tab.in_currency(12345, "0.0120") == 148
    assert tab.in_currency(250, "0.0100") == 3  # round(2.5) would be 2
    assert tab.in_currency(10300, "0.015") == 155  # 10300 * 0.015 is 154.49999999999997 in floats
    assert type(tab.in_currency(100, "1.5")) is int
