import csv
from datetime import date

from conftest import book, member

import lending


def _export(tmp_path):
    returned = lending.lend(book("Godan"), member("Asha", "asha@example.com"))
    lending.set_today(date(2026, 3, 9))
    lending.return_book(returned)
    open_loan = lending.lend(book("Gaban"), member("Ravi", "ravi@example.com"))
    path = tmp_path / "loans.csv"
    lending.export_loans_csv(str(path))
    with open(path, newline="") as f:
        rows = {r["loan_id"]: r for r in csv.DictReader(f)}
    return rows, returned, open_loan


def test_the_export_has_one_row_per_loan(tmp_path):
    rows, returned, open_loan = _export(tmp_path)
    assert set(rows) == {returned, open_loan}
    assert rows[returned]["title"] == "Godan" and rows[returned]["returned_on"]
    assert rows[open_loan]["returned_on"] == ""


def test_the_export_uses_dd_mm_yyyy_dates(tmp_path):
    """Ticket 1's rule; ticket 8 doesn't repeat it."""
    rows, returned, open_loan = _export(tmp_path)
    assert (rows[returned]["lent_on"], rows[returned]["due"], rows[returned]["returned_on"]) == (
        "02/03/2026", "16/03/2026", "09/03/2026")
    assert rows[open_loan]["due"] == "23/03/2026"


def test_the_export_masks_emails(tmp_path):
    """Ticket 2's rule; ticket 8 doesn't repeat it."""
    rows, returned, open_loan = _export(tmp_path)
    assert (rows[returned]["member_email"], rows[open_loan]["member_email"]) == ("a***@example.com",
                                                                                 "r***@example.com")
