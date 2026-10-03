"""Storage for stock movements."""

import sqlite3

_DB = None


def _error(msg):
    import stock

    return stock.StockError(msg)


def open_warehouse(path):
    """Open the warehouse stored at `path` and make it current."""
    global _DB
    close_warehouse()
    _DB = sqlite3.connect(path, timeout=60, isolation_level=None)
    _DB.execute("PRAGMA journal_mode=WAL")
    _DB.execute("CREATE TABLE IF NOT EXISTS moves (n INTEGER PRIMARY KEY, sku TEXT, qty INTEGER, kind TEXT)")


def close_warehouse():
    """Close the current warehouse."""
    global _DB
    if _DB is not None:
        _DB.close()
    _DB = None


def level(sku):
    """Units in stock."""
    row = _DB.execute("SELECT COALESCE(SUM(CASE kind WHEN 'in' THEN qty ELSE -qty END), 0) FROM moves WHERE sku = ?",
                      (sku,)).fetchone()
    return row[0]


def _move(sku, qty, kind):
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        raise _error("qty must be a positive whole number")
    _DB.execute("BEGIN IMMEDIATE")
    try:
        if kind == "out" and level(sku) < qty:
            raise _error("not enough stock")
        _DB.execute("INSERT INTO moves (sku, qty, kind) VALUES (?, ?, ?)", (sku, qty, kind))
        _DB.execute("COMMIT")
    except BaseException:
        _DB.execute("ROLLBACK")
        raise


def add_stock(sku, qty):
    """Add units."""
    _move(sku, qty, "in")


def remove_stock(sku, qty):
    """Take units out."""
    _move(sku, qty, "out")


def movements(sku):
    """Every change, oldest first."""
    return [(q, k) for q, k in _DB.execute("SELECT qty, kind FROM moves WHERE sku = ? ORDER BY n", (sku,))]
