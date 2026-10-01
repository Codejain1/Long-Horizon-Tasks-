import pytest
from conftest import book, member

import lending


def test_a_returned_reserved_book_goes_to_the_first_reserver_only():
    b, holder = book(), member()
    ravi, meera = member("Ravi", "ravi@example.com"), member("Meera", "meera@example.com")
    loan = lending.lend(b, holder)
    lending.reserve(b, ravi)
    lending.reserve(b, meera)
    lending.return_book(loan)
    with pytest.raises(lending.LendingError):
        lending.lend(b, meera)
    second = lending.lend(b, ravi)
    lending.return_book(second)
    lending.lend(b, meera)  # Ravi's reservation was used up
