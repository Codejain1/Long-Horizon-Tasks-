"""Stock levels, in whole units."""

_STOCK = {}


def add_item(name, qty):
    """Add `qty` units of `name` to stock."""
    if qty <= 0:
        raise ValueError("qty must be positive")
    _STOCK[name] = _STOCK.get(name, 0) + qty


def stock(name):
    """Units of `name` in stock."""
    return _STOCK.get(name, 0)


def reset():
    """Empty the stock (for tests)."""
    _STOCK.clear()
