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


def apply_discount(order, percent):
    """The order's total after a whole-number percentage discount, in cents, rounded half up."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be 0 to 100")
    return (order_total(order) * (100 - percent) * 2 + 100) // 200


def cancel_order(order_id):
    """Put the order's units back in stock and mark it cancelled."""
    order = _ORDERS[order_id]
    if order["status"] == "cancelled":
        raise ValueError("already cancelled")
    for name, qty in order["items"].items():
        inventory._STOCK[name] = inventory._STOCK.get(name, 0) + qty
    order["status"] = "cancelled"


def _stamp(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def export_orders_csv(path):
    """Write every order to a CSV file."""
    import csv

    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "status", "total_cents", "placed_at"])
        for o in _ORDERS.values():
            w.writerow([o["id"], o["status"], order_total(o), _stamp(o["placed_at"])])


def orders_placed_on(day):
    """Orders placed on this UTC day, oldest first."""
    found = [o for o in _ORDERS.values() if o["placed_at"].astimezone(timezone.utc).date() == day]
    return sorted(found, key=lambda o: o["placed_at"])


def refund(order_id, cents):
    """Refund part of an order."""
    order = _ORDERS[order_id]
    done = order.get("refunded_cents", 0)
    if cents <= 0 or done + cents > order_total(order):
        raise ValueError("bad refund")
    order["refunded_cents"] = done + cents


def daily_revenue(day):
    """Revenue for a UTC day: totals minus refunds, without cancelled orders."""
    return sum(order_total(o) - o.get("refunded_cents", 0) for o in orders_placed_on(day) if o["status"] != "cancelled")
