import ledger


def test_totals_are_integer_cents():
    lg = ledger.new_ledger()
    ledger.add_expense(lg, 1250, "food")
    ledger.add_expense(lg, 99, "food", note="snack")
    ledger.add_expense(lg, 5000, "rent")
    assert ledger.total(lg, "food") == 1349 and isinstance(ledger.total(lg, "food"), int)
    assert ledger.total(lg) == 6349 and isinstance(ledger.total(lg), int)
    assert ledger.total(lg, "travel") == 0
