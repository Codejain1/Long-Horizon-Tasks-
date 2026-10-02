import pytest
from conftest import event

import boxoffice


def test_cancel_frees_seats():
    e = event(seats=3, price="100.00")
    ref = boxoffice.book(e, "Asha", 3)
    assert boxoffice.sold(e) == 3
    boxoffice.cancel(ref)
    assert boxoffice.sold(e) == 0 and boxoffice.get_booking(ref)["status"] == "cancelled"
    with pytest.raises(boxoffice.BoxOfficeError):
        boxoffice.cancel(ref)
    boxoffice.book(e, "Ravi", 3)
