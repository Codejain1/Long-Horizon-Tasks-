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


def remove_item(name, qty):
    """Take `qty` units of `name` out of stock."""
    if qty <= 0 or _STOCK.get(name, 0) < qty:
        raise ValueError(f"cannot remove {qty} of {name}")
    _STOCK[name] -= qty


def low_stock(threshold):
    """Names of items with fewer than `threshold` units, sorted."""
    return sorted(n for n, q in _STOCK.items() if q < threshold)
