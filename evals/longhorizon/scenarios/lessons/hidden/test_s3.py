import stock


def test_stock_survives_reopening(tmp_path):
    path = str(tmp_path / "warehouse")
    stock.open_warehouse(path)
    stock.add_stock("A1", 5)
    stock.add_stock("A1", 3)
    stock.close_warehouse()
    stock.open_warehouse(path)
    assert (stock.level("A1"), stock.level("Z9")) == (8, 0)
    stock.close_warehouse()
