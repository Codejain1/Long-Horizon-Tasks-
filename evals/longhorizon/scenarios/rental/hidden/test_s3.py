from datetime import datetime

import pytest
from conftest import home, tenant

import rentals


def test_visits():
    p = home()
    t = tenant()
    late = rentals.schedule_visit(p, t, datetime(2026, 1, 3, 17, 0))
    early = rentals.schedule_visit(p, t, datetime(2026, 1, 3, 11, 30))
    assert [v["id"] for v in rentals.visits_for(p)] == [early, late]
    with pytest.raises(rentals.RentalError):
        rentals.schedule_visit(p, tenant("Kiran", "9000000001"), datetime(2026, 1, 3, 17, 45))
    with pytest.raises(rentals.RentalError):
        rentals.schedule_visit(p, tenant("Ravi", "9000000002", verified=False), datetime(2026, 1, 4, 10, 0))
