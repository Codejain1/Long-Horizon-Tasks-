"""The tempting storage, with nothing said about workers: one JSON file, read and rewritten on every change."""

import json
import os

_PATH = None


def _error(msg):
    import stock

    return stock.StockError(msg)


def _load():
    with open(_PATH) as fh:
        return json.load(fh)


def _save(data):
    with open(_PATH, "w") as fh:
        json.dump(data, fh)


def open_warehouse(path):
    global _PATH
    _PATH = path
    if not os.path.exists(path):
        _save({})


def close_warehouse():
    global _PATH
    _PATH = None


def level(sku):
    return sum(q if k == "in" else -q for q, k in _load().get(sku, []))


def _move(sku, qty, kind):
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        raise _error("qty must be a positive whole number")
    data = _load()
    if kind == "out" and sum(q if k == "in" else -q for q, k in data.get(sku, [])) < qty:
        raise _error("not enough stock")
    data.setdefault(sku, []).append([qty, kind])
    _save(data)


def add_stock(sku, qty):
    _move(sku, qty, "in")


def remove_stock(sku, qty):
    _move(sku, qty, "out")


def movements(sku):
    return [tuple(m) for m in _load().get(sku, [])]
