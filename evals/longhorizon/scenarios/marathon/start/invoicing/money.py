"""Money is integer cents; rounding happens in integers."""


def round_half_up(numerator: int, denominator: int) -> int:
    """`numerator / denominator` rounded to the nearest integer, halves up (non-negative values)."""
    return (2 * numerator + denominator) // (2 * denominator)


def is_cents(value: object) -> bool:
    """True for an int amount (bools aren't money)."""
    return isinstance(value, int) and not isinstance(value, bool)
