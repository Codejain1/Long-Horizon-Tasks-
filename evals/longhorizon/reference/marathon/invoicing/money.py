"""Money is integer cents; rounding happens in integers."""

import re

from invoicing.errors import ValidationError

RATE = re.compile(r"\d+(\.\d{1,4})?")


def round_half_up(numerator: int, denominator: int) -> int:
    """`numerator / denominator` rounded to the nearest integer, halves up (non-negative values)."""
    return (2 * numerator + denominator) // (2 * denominator)


def is_cents(value: object) -> bool:
    """True for an int amount (bools aren't money)."""
    return isinstance(value, int) and not isinstance(value, bool)


def rate_in_ten_thousandths(rate: str) -> int:
    """A decimal string with up to 4 places (`"83.1250"`) as an integer count of ten-thousandths."""
    if not isinstance(rate, str) or not RATE.fullmatch(rate):
        raise ValidationError(f"a rate is a decimal string with up to 4 places: {rate!r}")
    whole, _, fraction = rate.partition(".")
    return int(whole) * 10000 + int(fraction.ljust(4, "0"))
