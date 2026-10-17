from datetime import date

import pytest
from conftest import funded

import rentals


def test_moving_out_refunds_the_deposit_with_the_tenants_yield():
    a = funded()
    on = date(2026, 11, 30)
    shares = rentals.yield_split(a, on)
    deposit = rentals.get_agreement(a)["deposit_cents"]
    assert rentals.move_out(a, on, damages_cents=500000) == {
        "refund_cents": deposit - 500000 + shares["tenant_cents"], "owner_cents": 500000 + shares["owner_cents"]}
    assert rentals.yield_split(a, date(2027, 6, 1)) == shares  # nothing accrues after moving out
    with pytest.raises(rentals.RentalError):
        rentals.move_out(a, on)


def test_damages_cant_exceed_the_deposit():
    a = funded()
    with pytest.raises(rentals.RentalError):
        rentals.move_out(a, date(2026, 6, 1), damages_cents=rentals.get_agreement(a)["deposit_cents"] + 1)
