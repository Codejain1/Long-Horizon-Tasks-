"""Booking references: random, so they never clash across box offices and never need to change."""

import uuid


def new_ref():
    return uuid.uuid4().hex[:12].upper()
