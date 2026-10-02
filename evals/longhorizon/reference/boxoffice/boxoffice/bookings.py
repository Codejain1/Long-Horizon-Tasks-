"""Bookings."""

from boxoffice import store
from boxoffice.errors import BoxOfficeError
from boxoffice.events import _event, quote_for
from boxoffice.refs import new_ref


def book(event_id, name, qty):
    """Book `qty` seats; returns the booking reference."""
    _event(event_id)

    def make(event):
        q = quote_for(event, qty)
        return {"ref": new_ref(), "event": event_id, "name": name, "qty": qty, "status": "booked", **q}

    return store.add_booking(event_id, qty, make)["ref"]


def get_booking(ref):
    """The booking as a dict."""
    rec = store.get_booking(ref)
    if rec is None:
        raise BoxOfficeError(f"no booking {ref}")
    return rec


def cancel(ref):
    """Cancel a booking and free its seats."""
    get_booking(ref)
    store.set_booking_status(ref, "booked", "cancelled")


def sold(event_id):
    """Seats booked and not cancelled."""
    return sum(b["qty"] for b in store.bookings_for(event_id) if b["status"] == "booked")


def sales_report(event_id):
    """Bookings, seats and revenue over the bookings that aren't cancelled."""
    from decimal import Decimal

    from boxoffice.money import DECIMALS

    live = [b for b in store.bookings_for(event_id) if b["status"] == "booked"]
    currency = _event(event_id)["currency"]
    total = sum((Decimal(b["amount"]) for b in live), Decimal(0))
    places = Decimal(1).scaleb(-DECIMALS[currency]) if currency else Decimal(1)
    return {"bookings": len(live), "seats": sum(b["qty"] for b in live),
            "revenue": {"amount": str(total.quantize(places)), "currency": currency}}
