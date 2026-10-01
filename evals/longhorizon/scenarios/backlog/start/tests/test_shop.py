import pytest

import shop


def test_stock_and_orders():
    shop.add_item("tea", 10)
    order = shop.place_order({"tea": 3}, {"tea": 250})
    assert shop.stock("tea") == 7 and shop.order_total(order) == 750
    assert shop.get_order(order["id"]) is order


def test_cannot_oversell():
    shop.add_item("tea", 1)
    with pytest.raises(ValueError):
        shop.place_order({"tea": 2}, {"tea": 250})
