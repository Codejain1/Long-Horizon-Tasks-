import csv
from datetime import datetime, timezone

import shop
from conftest import order


def test_export_orders_csv(tmp_path):
    at = datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc)
    o = order({"tea": 2}, {"tea": 250}, placed_at=at)
    path = tmp_path / "orders.csv"
    shop.export_orders_csv(str(path))
    rows = list(csv.DictReader(path.open()))
    assert list(rows[0]) == ["id", "status", "total_cents", "placed_at"]
    assert rows == [{"id": o["id"], "status": "placed", "total_cents": "500", "placed_at": rows[0]["placed_at"]}]
    stamp = rows[0]["placed_at"]
    assert stamp.endswith("Z") and datetime.fromisoformat(stamp[:-1]).replace(tzinfo=timezone.utc) == at
