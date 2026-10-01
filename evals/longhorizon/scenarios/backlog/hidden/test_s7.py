import pytest

import shop
from conftest import order


def test_partial_refunds():
    o = order({"tea": 2}, {"tea": 250})
    shop.refund(o["id"], 100)
    shop.refund(o["id"], 150)
    assert shop.get_order(o["id"])["refunded_cents"] == 250
    for bad in (251, 0, -5):
        with pytest.raises(ValueError):
            shop.refund(o["id"], bad)
    assert shop.get_order(o["id"])["refunded_cents"] == 250
