from datetime import date

import pytest

import rentals


def test_owners_tenants_and_properties():
    owner = rentals.add_owner("Meera", "9876543210")
    tenant = rentals.add_tenant("Arjun", "9123456789")
    home = rentals.add_property(owner, "2BHK, Indiranagar", "Bengaluru", "KA", "residential", 2500000)
    assert rentals.get_tenant(tenant)["phone"] == "9123456789"
    assert rentals.get_property(home)["owner"] == owner and rentals.get_property(home)["available"]


def test_bad_input_is_refused():
    with pytest.raises(rentals.RentalError):
        rentals.add_owner("Meera", "12345")
    owner = rentals.add_owner("Meera", "9876543210")
    with pytest.raises(rentals.RentalError):
        rentals.add_property(owner, "Shop", "Lucknow", "UP", "industrial", 100)


def test_today_can_be_pinned():
    rentals.set_today(date(2026, 3, 1))
    assert rentals.today() == date(2026, 3, 1)
