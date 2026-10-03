import pytest

import stock


def test_remove_and_movements(tmp_path):
    stock.open_warehouse(str(tmp_path / "warehouse"))
    stock.add_stock("A1", 5)
    stock.remove_stock("A1", 2)
    with pytest.raises(stock.StockError):
        stock.remove_stock("A1", 9)
    assert stock.level("A1") == 3 and stock.movements("A1") == [(5, "in"), (2, "out")]
    stock.close_warehouse()
