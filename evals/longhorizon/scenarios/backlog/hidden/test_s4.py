import shop


def test_low_stock_sorted():
    for name, qty in (("tea", 2), ("cup", 10), ("jam", 1), ("pen", 5)):
        shop.add_item(name, qty)
    assert shop.low_stock(5) == ["jam", "tea"]
    assert shop.low_stock(1) == []
