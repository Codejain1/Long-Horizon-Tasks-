import pytest
from conftest import home, tenant

import rentals


def test_offers():
    p = home()
    first = rentals.make_offer(p, tenant(), 2400000)
    second = rentals.make_offer(p, tenant("Kiran", "9000000001"), 2500000)
    rentals.accept_offer(second)
    assert rentals.get_offer(second)["status"] == "accepted" and rentals.get_offer(first)["status"] == "rejected"
    assert not rentals.get_property(p)["available"] and rentals.search_properties() == []
    with pytest.raises(rentals.RentalError):
        rentals.make_offer(p, tenant("Ravi", "9000000002"), 2600000)
    with pytest.raises(rentals.RentalError):
        rentals.make_offer(home(title="Other"), tenant("Asha", "9000000003", verified=False), 2000000)
