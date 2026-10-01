import pytest
from conftest import agreement

import rentals


def test_funding_splits_the_deposit_once():
    a = agreement()
    got = rentals.fund_deposit(a)
    assert got["held_cents"] + got["deployed_cents"] == rentals.get_agreement(a)["deposit_cents"]
    with pytest.raises(rentals.RentalError):
        rentals.fund_deposit(a)


def test_a_quarter_of_the_deposit_stays_in_reserve():
    """Ticket 3's rule; ticket 7 doesn't repeat it."""
    assert rentals.fund_deposit(agreement()) == {"held_cents": 2500000, "deployed_cents": 7500000}


def test_without_consent_nothing_is_deployed():
    """Ticket 2's rule; ticket 7 doesn't repeat it."""
    assert rentals.fund_deposit(agreement(consent=False)) == {"held_cents": 10000000, "deployed_cents": 0}
