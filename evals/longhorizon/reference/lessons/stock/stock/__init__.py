"""Warehouse stock levels: one SQLite file, safe for several worker processes."""

from stock.store import add_stock, close_warehouse, level, movements, open_warehouse, remove_stock


class StockError(Exception):
    """Anything the stock service refuses to do."""
