from datetime import date

import pytest
from conftest import book, member

import lending


def test_loans_of_lists_active_loans_soonest_due_first():
    m = member()
    first = lending.lend(book("A"), m)
    lending.set_today(date(2026, 3, 5))
    second = lending.lend(book("B"), m)
    returned = lending.lend(book("C"), m)
    lending.return_book(returned)
    assert [l["id"] for l in lending.loans_of(m)] == [first, second]


def test_a_member_holds_at_most_three_books():
    m = member()
    for title in "ABC":
        lending.lend(book(title), m)
    with pytest.raises(lending.LendingError):
        lending.lend(book("D"), m)
