import pytest

import shop


def test_remove_item():
    shop.add_item("tea", 5)
    shop.remove_item("tea", 2)
    assert shop.stock("tea") == 3
    for bad in (4, 0, -1):
        with pytest.raises(ValueError):
            shop.remove_item("tea", bad)
    assert shop.stock("tea") == 3
