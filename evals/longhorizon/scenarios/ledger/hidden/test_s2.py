import ledger


def test_save_load_round_trip(tmp_path):
    lg = ledger.new_ledger()
    ledger.add_expense(lg, 1250, "food")
    ledger.add_expense(lg, 5000, "rent", note="march")
    ledger.save(lg, tmp_path / "l.dat")
    back = ledger.load(tmp_path / "l.dat")
    assert ledger.total(back) == 6250 and isinstance(ledger.total(back), int)
    assert ledger.total(back, "rent") == 5000
