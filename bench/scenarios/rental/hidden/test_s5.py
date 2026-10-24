from conftest import accepted, home

import rentals


def test_the_quote_is_first_rent_plus_deposit():
    q = rentals.move_in_quote(accepted(rent=2400000))
    assert q["rent_cents"] == 2400000 and q["total_cents"] == q["rent_cents"] + q["deposit_cents"]


def test_the_deposit_is_four_months_rent():
    """Ticket 1's rule; ticket 5 doesn't repeat it."""
    assert rentals.move_in_quote(accepted(home(rent=2500000)))["deposit_cents"] == 10000000
    shop = home(state="UP", kind="commercial", rent=1000000, title="Shop", city="Lucknow")
    assert rentals.move_in_quote(accepted(shop))["deposit_cents"] == 4000000


def test_residential_deposits_are_capped_in_model_tenancy_act_states():
    """Ticket 2's rule; ticket 5 doesn't repeat it."""
    flat = home(state="UP", rent=1500000, title="Flat", city="Lucknow")
    assert rentals.move_in_quote(accepted(flat))["deposit_cents"] == 3000000
