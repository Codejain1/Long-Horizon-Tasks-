"""Where the service keeps its data: one dict of tables, in memory."""

from __future__ import annotations

TABLES = ("customers", "products", "invoices")
_DATA: dict[str, dict] = {}


def data() -> dict[str, dict]:
    """The tables, keyed by record id. Callers read and change them in place."""
    for table in TABLES:
        _DATA.setdefault(table, {})
    return _DATA


def reset() -> None:
    """Empty every table."""
    _DATA.clear()
