"""Exchange rates to INR, kept as decimal strings and applied in integers."""

from __future__ import annotations

from invoicing.errors import ValidationError
from invoicing.invoices import _get, _totals
from invoicing.money import rate_in_ten_thousandths, round_half_up
from invoicing.store import transaction


def set_fx_rate(currency: str, rate: str) -> None:
    """Set how many INR one unit of `currency` is worth, as a decimal string with up to 4 places."""
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        raise ValidationError(f"a currency is a 3-letter code: {currency!r}")
    if rate_in_ten_thousandths(rate) <= 0:
        raise ValidationError("a rate must be positive")
    with transaction() as db:
        db["fx"][currency] = rate


def total_in_inr(invoice_id: str) -> int:
    """The invoice's total in INR cents, half up."""
    with transaction() as db:
        invoice = _get(db, invoice_id)
        return _to_inr(db, invoice["currency"], _totals(invoice)["total_cents"])


def _to_inr(db: dict, currency: str, cents: int) -> int:
    if currency == "INR":
        return cents
    rate = db["fx"].get(currency)
    if rate is None:
        raise ValidationError(f"no exchange rate for {currency}")
    return round_half_up(cents * rate_in_ten_thousandths(rate), 10000)
