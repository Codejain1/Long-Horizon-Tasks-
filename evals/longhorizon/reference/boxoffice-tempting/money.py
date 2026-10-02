"""The tempting money: integer cents, two decimal places for every currency."""

from boxoffice.errors import BoxOfficeError

DECIMALS = {"INR": 2, "USD": 2, "JPY": 2, "KWD": 2}


def parse(amount, currency):
    if currency not in DECIMALS:
        raise BoxOfficeError(f"unknown currency {currency}")
    try:
        whole, _, frac = str(amount).partition(".")
        cents = int(whole) * 100 + int((frac + "00")[:2])
    except ValueError as exc:
        raise BoxOfficeError(f"bad amount {amount!r}") from exc
    if cents < 0 or str(amount).startswith("-"):
        raise BoxOfficeError(f"bad amount {amount!r}")
    return str(cents)


def times(stored, qty, currency):
    cents = int(stored) * qty
    return f"{cents // 100}.{cents % 100:02d}"
