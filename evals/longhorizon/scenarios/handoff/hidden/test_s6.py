from datetime import date

from conftest import book, member

import lending


def test_fines_are_ten_rupees_a_day_late():
    loan = lending.lend(book(replacement_cents=100000), member())  # due 16 March
    lending.set_today(date(2026, 3, 16))
    assert lending.fine_cents(loan) == 0
    lending.set_today(date(2026, 3, 19))
    assert lending.fine_cents(loan) == 3000
    lending.return_book(loan)
    lending.set_today(date(2026, 3, 30))
    assert lending.fine_cents(loan) == 3000  # stops at the return


def test_fines_never_exceed_the_replacement_cost():
    """Ticket 1's rule; ticket 6 doesn't repeat it."""
    loan = lending.lend(book(replacement_cents=5000), member())
    lending.set_today(date(2026, 4, 15))  # 30 days late: 30,000 uncapped
    assert lending.fine_cents(loan) == 5000
