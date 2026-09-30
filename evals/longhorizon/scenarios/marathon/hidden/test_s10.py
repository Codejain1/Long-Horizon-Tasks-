import pytest

import invoicing
from conftest import draft


def test_totals_convert_to_inr_half_up():
    invoicing.set_fx_rate("USD", "83.1250")
    usd = draft(1999, currency="USD")
    assert invoicing.get_invoice(usd)["currency"] == "USD"
    assert invoicing.total_in_inr(usd) == 166167  # 166166.875
    invoicing.set_fx_rate("JPY", "0.5")
    assert invoicing.total_in_inr(draft(5, currency="JPY")) == 3  # 2.5 rounds up
    inr = draft(1234)
    assert invoicing.total_in_inr(inr) == 1234 and invoicing.get_invoice(inr)["currency"] == "INR"


def test_a_currency_without_a_rate_is_refused():
    with pytest.raises(ValueError):
        invoicing.total_in_inr(draft(100, currency="EUR"))
