from datetime import datetime, timedelta

import pytest
from conventions import UTC, is_cents, is_id, is_stamp

import subscriptions

START = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)


def test_subscription_follows_the_team_rules(capsys):
    sub = subscriptions.subscribe("acme", 4900, START)
    is_id(sub["id"])
    is_cents(sub["plan_cents"], 4900)
    is_stamp(sub["started_at"], START)
    is_stamp(sub["renews_at"], START + timedelta(days=30))
    assert capsys.readouterr().out == ""


def test_renew_moves_forward_thirty_days():
    sub = subscriptions.subscribe("acme", 4900, START)
    nxt = subscriptions.renew(sub)
    is_stamp(nxt["renews_at"], START + timedelta(days=60))
    assert abs(is_stamp(nxt["renewed_at"]) - datetime.now(UTC)) < timedelta(minutes=5)


def test_monthly_revenue_is_cents():
    subs = [subscriptions.subscribe("a", 4900, START), subscriptions.subscribe("b", 101, START)]
    is_cents(subscriptions.monthly_revenue(subs), 5001)


def test_bad_plan_raises_value_error():
    with pytest.raises(ValueError):
        subscriptions.subscribe("acme", 0, START)
