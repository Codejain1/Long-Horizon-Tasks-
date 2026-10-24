from datetime import date

import pytest
from conftest import agreement, funded

import rentals


def test_rent_payments():
    a = agreement(months=3)
    rentals.pay_rent(a, date(2026, 2, 1))
    rentals.pay_rent(a, date(2026, 1, 1))
    assert rentals.rent_paid(a) == [date(2026, 1, 1), date(2026, 2, 1)]
    with pytest.raises(rentals.RentalError):
        rentals.pay_rent(a, date(2026, 1, 1))
    with pytest.raises(rentals.RentalError):
        rentals.pay_rent(a, date(2026, 4, 1))


def test_from_april_the_partner_pays_10_percent_and_shares_stay():
    """The rate change is ticket 9's; the tenant's and owner's shares are ticket 1's."""
    rentals.set_today(date(2026, 4, 1))
    a = funded()
    assert rentals.yield_split(a, date(2027, 4, 1)) == {
        "tenant_cents": 150000, "owner_cents": 225000, "platform_cents": 375000}
