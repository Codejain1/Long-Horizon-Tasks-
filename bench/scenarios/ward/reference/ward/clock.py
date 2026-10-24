"""The ward's clock. Times are timezone-aware UTC; tests set the time with `set_now`."""

from datetime import datetime, timezone

_NOW = None


def set_now(when):
    """Freeze the clock at `when` (an aware datetime), or unfreeze it with None."""
    global _NOW
    if when is not None and when.tzinfo is None:
        raise ValueError("set_now needs an aware datetime")
    _NOW = when


def now():
    """The current time, in UTC."""
    return (_NOW or datetime.now(timezone.utc)).astimezone(timezone.utc)


def stamp(when):
    """An ISO-8601 UTC string ending in Z, like 2026-03-10T08:00:00Z."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
