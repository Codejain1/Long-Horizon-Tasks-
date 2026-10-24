"""Today's date in IST. Tests pin it with `set_today`."""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
_today: Optional[date] = None


def set_today(day: Optional[date]) -> None:
    global _today
    _today = day


def today() -> date:
    return _today or datetime.now(IST).date()
