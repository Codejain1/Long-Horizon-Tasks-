from datetime import timedelta

import pytest
from conftest import DAY, book, member

import lending


def test_renewing_adds_two_weeks_to_the_due_date_at_most_twice():
    loan = lending.lend(book(), member())
    assert lending.renew(loan) == DAY + timedelta(days=28)
    assert lending.renew(loan) == DAY + timedelta(days=42)
    assert lending.get_loan(loan)["due"] == DAY + timedelta(days=42)
    with pytest.raises(lending.LendingError):
        lending.renew(loan)


def test_a_reserved_book_cannot_be_renewed():
    b = book()
    loan = lending.lend(b, member())
    lending.reserve(b, member("Ravi", "ravi@example.com"))
    with pytest.raises(lending.LendingError):
        lending.renew(loan)
