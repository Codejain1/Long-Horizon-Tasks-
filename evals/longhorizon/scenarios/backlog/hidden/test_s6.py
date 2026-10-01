from datetime import date, datetime, timezone

import shop
from conftest import order


def test_orders_placed_on_uses_utc_days():
    late = order({"a": 1}, {"a": 1}, placed_at=datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc))  # Mar 2 in IST
    early = order({"b": 1}, {"b": 1}, placed_at=datetime(2026, 3, 1, 1, 0, tzinfo=timezone.utc))
    order({"c": 1}, {"c": 1}, placed_at=datetime(2026, 3, 2, 0, 30, tzinfo=timezone.utc))
    assert [o["id"] for o in shop.orders_placed_on(date(2026, 3, 1))] == [early["id"], late["id"]]
