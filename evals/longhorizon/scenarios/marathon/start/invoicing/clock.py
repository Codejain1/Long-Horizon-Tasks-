"""The service's clock: timezone-aware UTC, pinned in tests."""

from __future__ import annotations

from datetime import datetime, timezone

from invoicing.errors import ValidationError

_PINNED: datetime | None = None


def now() -> datetime:
    """The current time in UTC (the pinned time, if tests set one)."""
    return _PINNED or datetime.now(timezone.utc)


def set_now(when: datetime | None) -> None:
    """Pin the clock to `when` (timezone-aware), or unpin it with None."""
    global _PINNED
    if when is not None and when.tzinfo is None:
        raise ValidationError("the clock needs a timezone-aware time")
    _PINNED = when


def iso(when: datetime) -> str:
    """`when` as an ISO-8601 UTC string ending in Z, to the second."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
