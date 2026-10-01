from datetime import date

import pytest

import lending


def test_members_and_books():
    m = lending.add_member("Asha", "asha@example.com")
    b = lending.add_book("Malgudi Days", "R. K. Narayan", 45000)
    assert lending.get_member(m)["email"] == "asha@example.com"
    assert lending.get_book(b)["replacement_cents"] == 45000


def test_bad_input_is_refused():
    with pytest.raises(lending.LendingError):
        lending.add_member("Asha", "no-at-sign")
    with pytest.raises(lending.LendingError):
        lending.add_book("Untitled", "Anon", 0)


def test_today_can_be_pinned():
    lending.set_today(date(2026, 3, 1))
    assert lending.today() == date(2026, 3, 1)
