"""Today's date in UTC. Tests pin it with `set_today`."""

from datetime import date, datetime, timezone
from typing import Optional

_today: Optional[date] = None


def set_today(day: Optional[date]) -> None:
    global _today
    _today = day


def today() -> date:
    return _today or datetime.now(timezone.utc).date()
