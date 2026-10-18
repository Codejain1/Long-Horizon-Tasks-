"""The business and legal rules from the early tickets, in one place."""

from datetime import date

from rentals.states import MTA_STATES

DEPOSIT_MONTHS = 4  # ticket 1
MTA_RESIDENTIAL_CAP_MONTHS = 2  # ticket 2
MAX_TERM_MONTHS = 11  # ticket 1: longer agreements need registration
RESERVE_PCT = 25  # ticket 3: kept back for same-day refunds
TENANT_PCT, OWNER_PCT = 2, 3  # ticket 1: of the partner's yield, per year
RATE_CHANGE = date(2026, 4, 1)  # ticket 9


def deposit_months(state: str, kind: str) -> int:
    if kind == "residential" and state in MTA_STATES:
        return min(DEPOSIT_MONTHS, MTA_RESIDENTIAL_CAP_MONTHS)
    return DEPOSIT_MONTHS


def max_term_months() -> int:
    return MAX_TERM_MONTHS


def deployable_cents(deposit_cents: int, consent: bool) -> int:
    """What goes to the lending partner: nothing without the tenant's consent (ticket 2)."""
    return deposit_cents * (100 - RESERVE_PCT) // 100 if consent else 0


def partner_rate_pct(day: date) -> int:
    return 10 if day >= RATE_CHANGE else 9


def shares_pct() -> tuple:
    return TENANT_PCT, OWNER_PCT


def masked_phone(phone: str) -> str:
    """Ticket 2: phones leaving the platform keep the first and last two digits."""
    return phone[:2] + "*" * (len(phone) - 4) + phone[-2:]


def masked_pan(pan: str) -> str:
    return "X" * 6 + pan[-4:]


def masked_aadhaar(aadhaar: str) -> str:
    return "X" * 8 + aadhaar[-4:]
