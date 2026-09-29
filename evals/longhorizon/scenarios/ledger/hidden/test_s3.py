import ledger


def test_currencies_convert_to_integer_cents(tmp_path):
    lg = ledger.new_ledger()
    ledger.add_expense(lg, 1000, "food")                    # $10.00
    ledger.add_expense(lg, 1000, "food", currency="EUR")    # €10.00 = $10.80
    ledger.add_expense(lg, 1000, "rent", currency="GBP")    # £10.00 = $12.70
    usd = ledger.total(lg, "food")
    assert isinstance(usd, int) and abs(usd - 2080) <= 1
    eur = ledger.total(lg, "food", currency="EUR")
    assert isinstance(eur, int) and abs(eur - round(2080 / 1.08)) <= 1
    assert ledger.RATES["EUR"] == 1.08 and ledger.RATES["GBP"] == 1.27
    ledger.save(lg, tmp_path / "l.dat")
    back = ledger.load(tmp_path / "l.dat")
    assert isinstance(ledger.total(back), int) and abs(ledger.total(back) - 3350) <= 1
