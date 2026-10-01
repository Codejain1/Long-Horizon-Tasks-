import re
from datetime import date, timedelta

import pytest
from conftest import DAY, book, member

import lending


def test_lend_sets_a_due_date_two_weeks_out_and_return_closes_the_loan():
    loan = lending.lend(book(), member())
    assert re.fullmatch(r"[0-9a-f]{32}", loan)
    assert lending.get_loan(loan)["due"] == DAY + timedelta(days=14)
    assert lending.get_loan(loan)["returned_on"] is None
    lending.set_today(date(2026, 3, 9))
    lending.return_book(loan)
    assert lending.get_loan(loan)["returned_on"] == date(2026, 3, 9)


def test_a_book_on_loan_cannot_be_lent_again_until_returned():
    b, m = book(), member()
    loan = lending.lend(b, m)
    with pytest.raises(lending.LendingError):
        lending.lend(b, member("Ravi", "ravi@example.com"))
    lending.return_book(loan)
    lending.lend(b, member("Ravi", "ravi@example.com"))
