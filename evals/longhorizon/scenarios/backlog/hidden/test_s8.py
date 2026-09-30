from datetime import date, datetime, timezone

import shop
from conftest import order

DAY = datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc)


def test_daily_revenue():
    a = order({"a": 2}, {"a": 500}, placed_at=DAY)
    b = order({"b": 1}, {"b": 300}, placed_at=DAY)
    order({"c": 1}, {"c": 999}, placed_at=datetime(2026, 3, 2, 1, 0, tzinfo=timezone.utc))
    shop.refund(a["id"], 200)
    shop.cancel_order(b["id"])
    assert shop.daily_revenue(date(2026, 3, 1)) == 800
