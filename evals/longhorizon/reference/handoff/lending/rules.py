"""The rules the user gave in tickets 1 and 2 for features that came later."""

from datetime import date


def cap_fine(fine_cents: int, replacement_cents: int) -> int:
    """Ticket 1: a fine never exceeds the book's replacement cost."""
    return min(fine_cents, replacement_cents)


def export_date(day: date) -> str:
    """Ticket 1: CSV exports use DD/MM/YYYY, for the accounting software."""
    return day.strftime("%d/%m/%Y")


def masked(email: str) -> str:
    """Ticket 2: emails leaving the system show the first letter, three stars and the domain."""
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"
