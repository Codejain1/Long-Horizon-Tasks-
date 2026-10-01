import pytest
from conftest import AADHAAR, PAN

import rentals
from rentals import store


def test_verification():
    t = rentals.add_tenant("Arjun", "9123456789")
    assert not rentals.get_tenant(t).get("verified")
    rentals.verify_tenant(t, PAN, AADHAAR)
    assert rentals.get_tenant(t)["verified"] is True
    for pan, aadhaar in [("ABCD1234F", AADHAAR), (PAN, "12345678901"), (PAN, "034567890123")]:
        with pytest.raises(rentals.RentalError):
            rentals.verify_tenant(t, pan, aadhaar)


def test_pan_and_aadhaar_are_never_stored_in_full():
    t = rentals.add_tenant("Arjun", "9123456789")
    rentals.verify_tenant(t, PAN, AADHAAR)
    assert rentals.get_tenant(t)["pan"] == "XXXXXX234F" and rentals.get_tenant(t)["aadhaar"] == "XXXXXXXX0123"
    assert PAN not in repr(store._TABLES) and AADHAAR not in repr(store._TABLES)
