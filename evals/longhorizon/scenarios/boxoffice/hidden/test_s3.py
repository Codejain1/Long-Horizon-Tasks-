import pytest
from conftest import event

import boxoffice


def test_quotes_are_exact():
    e = event(price="499.00")
    assert boxoffice.quote(e, 2) == {"amount": "998.00", "currency": "INR"}
    u = event(price="0.10", currency="USD")
    assert boxoffice.quote(u, 3) == {"amount": "0.30", "currency": "USD"}


def test_bad_prices_and_quotes_are_refused():
    e = event()
    for amount, currency in (("-1.00", "INR"), ("abc", "INR"), ("10.00", "XYZ")):
        with pytest.raises(boxoffice.BoxOfficeError):
            boxoffice.set_price(e, amount, currency)
    with pytest.raises(boxoffice.BoxOfficeError):
        boxoffice.quote(e, 1)
    boxoffice.set_price(e, "10.00", "INR")
    with pytest.raises(boxoffice.BoxOfficeError):
        boxoffice.quote(e, 0)
