from datetime import datetime, timedelta

import pytest
from conventions import UTC, is_cents, is_id, is_instant

import payouts

WHEN = datetime(2026, 6, 30, 20, 0, tzinfo=UTC)


def test_payout_follows_the_team_rules(capsys):
    p = payouts.schedule_payout("shop", 25000, WHEN)
    is_id(p["id"])
    is_cents(p["amount_cents"], 25000)
    is_instant(p["scheduled_for"], WHEN)
    assert abs(is_instant(p["created_at"]) - datetime.now(UTC)) < timedelta(minutes=5)
    assert capsys.readouterr().out == ""


def test_due_payouts_is_inclusive_and_instant_based():
    a = payouts.schedule_payout("a", 1, WHEN)
    b = payouts.schedule_payout("b", 1, WHEN + timedelta(minutes=1))
    assert [p["merchant"] for p in payouts.due_payouts([a, b], WHEN)] == ["a"]


def test_split_is_exact_integer_cents():
    parts = payouts.split(1000, [1, 1, 1])
    assert sum(parts) == 1000 and all(type(x) is int for x in parts) and max(parts) - min(parts) <= 1


def test_bad_split_raises_value_error():
    with pytest.raises(ValueError):
        payouts.split(1000, [])
