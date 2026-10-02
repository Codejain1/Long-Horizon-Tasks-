"""The tempting references: short sequential numbers, easy to read out at the counter."""

from boxoffice import store


def new_ref():
    data = store._load()
    return f"BK-{len(data['bookings']) + 1:05d}"
