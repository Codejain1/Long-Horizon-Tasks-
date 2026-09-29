from datetime import datetime, timedelta

import pytest
from conventions import UTC, is_cents, is_id, is_stamp

import invoices

DUE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def test_invoice_follows_the_team_rules(capsys):
    inv = invoices.create_invoice("acme", 12999, DUE)
    is_id(inv["id"])
    is_cents(inv["amount_cents"], 12999)
    is_stamp(inv["due"], DUE)
    assert abs(is_stamp(inv["created_at"]) - datetime.now(UTC)) < timedelta(minutes=5)
    assert inv["status"] == "open" and inv["customer"] == "acme"
    assert capsys.readouterr().out == ""


def test_overdue_compares_real_instants():
    early = invoices.create_invoice("a", 100, DUE)
    late = invoices.create_invoice("b", 200, DUE + timedelta(days=2))
    got = invoices.overdue([early, late], DUE + timedelta(hours=1))
    assert [i["customer"] for i in got] == ["a"]


def test_json_round_trip():
    inv = invoices.create_invoice("acme", 5, DUE)
    assert invoices.from_json(invoices.to_json(inv)) == inv


def test_bad_amount_raises_value_error():
    with pytest.raises(ValueError):
        invoices.create_invoice("acme", -5, DUE)
