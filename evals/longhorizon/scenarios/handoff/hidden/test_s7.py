from datetime import date

from conftest import book, member

import lending


def _two_late():
    a = lending.lend(book("Godan"), member("Asha", "asha@example.com"))  # due 16 March
    lending.set_today(date(2026, 3, 10))
    lending.lend(book("Gaban"), member("Ravi", "ravi@example.com"))  # due 24 March
    lending.lend(book("Nirmala"), member("Meera", "meera@example.com"))
    lending.set_today(date(2026, 3, 26))
    return a


def test_the_overdue_report_lists_late_loans_most_overdue_first():
    _two_late()
    rows = lending.overdue_report()
    assert [(r["title"], r["member_name"], r["days_overdue"]) for r in rows] == [
        ("Godan", "Asha", 10), ("Gaban", "Ravi", 2), ("Nirmala", "Meera", 2)] or \
        [(r["title"], r["member_name"], r["days_overdue"]) for r in rows] == [
        ("Godan", "Asha", 10), ("Nirmala", "Meera", 2), ("Gaban", "Ravi", 2)]
    assert rows[0]["due"] == date(2026, 3, 16)


def test_the_overdue_report_masks_emails():
    """Ticket 2's rule; ticket 7 doesn't repeat it."""
    _two_late()
    assert sorted(r["email"] for r in lending.overdue_report()) == ["a***@example.com", "m***@example.com",
                                                                     "r***@example.com"]
