import pytest

import shop
from conftest import order


def test_cancel_order_restocks_once():
    o = order({"tea": 3}, {"tea": 250})
    assert shop.stock("tea") == 0
    shop.cancel_order(o["id"])
    assert shop.stock("tea") == 3 and shop.get_order(o["id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        shop.cancel_order(o["id"])
    assert shop.stock("tea") == 3
