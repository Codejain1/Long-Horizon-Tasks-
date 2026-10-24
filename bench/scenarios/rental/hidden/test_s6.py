from datetime import date

import pytest
from conftest import accepted, agreement

import rentals


def test_agreements():
    a = rentals.get_agreement(agreement(months=6, start=date(2026, 3, 1)))
    assert (a["start"], a["end"]) == (date(2026, 3, 1), date(2026, 8, 31))
    with pytest.raises(rentals.RentalError):
        rentals.create_agreement(accepted(), date(2026, 3, 15), 6)
    assert rentals.get_agreement(agreement(months=11))["end"] == date(2026, 11, 30)


def test_agreements_longer_than_11_months_are_refused():
    """Ticket 1's rule; ticket 6 doesn't repeat it."""
    with pytest.raises(rentals.RentalError):
        rentals.create_agreement(accepted(), date(2026, 3, 1), 12)
