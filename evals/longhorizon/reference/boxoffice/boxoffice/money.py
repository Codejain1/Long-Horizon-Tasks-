"""Money: exact decimals, with each currency's own number of decimal places."""

from decimal import Decimal, InvalidOperation

from boxoffice.errors import BoxOfficeError

DECIMALS = {"INR": 2, "USD": 2, "JPY": 0, "KWD": 3}


def parse(amount, currency):
    """The amount as an exact value, or BoxOfficeError."""
    if currency not in DECIMALS:
        raise BoxOfficeError(f"unknown currency {currency}")
    try:
        value = Decimal(str(amount))
    except InvalidOperation as exc:
        raise BoxOfficeError(f"bad amount {amount!r}") from exc
    if not value.is_finite() or value < 0 or value != value.quantize(Decimal(1).scaleb(-DECIMALS[currency])):
        raise BoxOfficeError(f"bad amount {amount!r} for {currency}")
    return str(value)


def times(stored, qty, currency):
    """`qty` times a stored price, formatted in the currency's decimals."""
    return str((Decimal(stored) * qty).quantize(Decimal(1).scaleb(-DECIMALS[currency])))
