import pytest
from conftest import event

import boxoffice


def test_book_and_get():
    e = event(seats=3, price="250.00")
    ref = boxoffice.book(e, "Asha", 2)
    b = boxoffice.get_booking(ref)
    assert (b["ref"], b["event"], b["name"], b["qty"], b["status"], b["amount"], b["currency"]) == (
        ref, e, "Asha", 2, "booked", "500.00", "INR")
    with pytest.raises(boxoffice.BoxOfficeError):
        boxoffice.book(e, "Ravi", 2)
