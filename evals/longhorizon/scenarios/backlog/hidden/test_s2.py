import pytest

import shop
from conftest import order


def test_apply_discount_rounds_half_up_in_cents():
    o = order({"pen": 1}, {"pen": 125})
    assert shop.apply_discount(o, 10) == 113 and type(shop.apply_discount(o, 10)) is int
    assert shop.apply_discount(o, 0) == 125 and shop.apply_discount(o, 100) == 0
    big = order({"box": 3}, {"box": 333})
    assert shop.apply_discount(big, 15) == 849
    for bad in (-1, 101):
        with pytest.raises(ValueError):
            shop.apply_discount(o, bad)
