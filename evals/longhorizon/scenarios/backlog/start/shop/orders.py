"""Orders. Prices are integer cents per unit."""
import uuid
from datetime import datetime, timezone

from shop import inventory

_ORDERS = {}


def place_order(items, prices):
    """Place an order for {name: qty}, priced from {name: cents per unit}; takes the units out of stock."""
    for name, qty in items.items():
        if qty <= 0 or inventory.stock(name) < qty:
            raise ValueError(f"cannot order {qty} of {name}")
    for name, qty in items.items():
        inventory._STOCK[name] -= qty
    order = {"id": uuid.uuid4().hex, "items": dict(items), "prices": {n: prices[n] for n in items},
             "status": "placed", "placed_at": datetime.now(timezone.utc)}
    _ORDERS[order["id"]] = order
    return order


def order_total(order):
    """The order's total in cents."""
    return sum(order["prices"][name] * qty for name, qty in order["items"].items())


def get_order(order_id):
    """The order with this id; raises KeyError if there is none."""
    return _ORDERS[order_id]


def reset_orders():
    """Forget all orders (for tests)."""
    _ORDERS.clear()
