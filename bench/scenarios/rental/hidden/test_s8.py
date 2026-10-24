from datetime import date

import pytest
from conftest import DAY, funded

import rentals

KEYS = {"tenant_cents", "owner_cents", "platform_cents"}


def test_the_yield_grows_from_funding():
    a = funded()
    assert rentals.yield_split(a, DAY) == dict.fromkeys(KEYS, 0)
    early, later = rentals.yield_split(a, date(2026, 2, 1)), rentals.yield_split(a, date(2026, 3, 1))
    assert set(early) == KEYS and sum(later.values()) > sum(early.values()) > 0
    with pytest.raises(rentals.RentalError):
        rentals.yield_split(a, date(2025, 12, 31))


def test_the_partner_pays_9_percent_split_2_3_and_the_rest():
    """Ticket 1's rule; ticket 8 doesn't repeat it. 59 days on 75,000 rupees deployed."""
    assert rentals.yield_split(funded(), date(2026, 3, 1)) == {
        "tenant_cents": 24246, "owner_cents": 36369, "platform_cents": 48494}
