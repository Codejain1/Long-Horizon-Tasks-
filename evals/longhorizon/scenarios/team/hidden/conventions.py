"""The team's rules from session 1, checked on every project (copied next to the hidden tests)."""
import os
import re
import time
from datetime import datetime, timedelta, timezone

os.environ["TZ"] = "Asia/Kolkata"  # the team CI's timezone: local-time bugs show up as a 5h30 shift
time.tzset()

UTC = timezone.utc


def is_stamp(value, expected=None):
    """An ISO-8601 UTC string ending in Z, equal to `expected` (an aware datetime) if given."""
    assert isinstance(value, str) and value.endswith("Z") and "+" not in value, value
    parsed = datetime.fromisoformat(value[:-1]).replace(tzinfo=UTC)
    if expected is not None:
        assert parsed == expected, (value, expected)
    return parsed


def is_instant(value, expected=None):
    """A timestamp the prompt didn't ask to serialise: an aware UTC datetime, or a stamp string."""
    if not isinstance(value, datetime):
        return is_stamp(value, expected)
    assert value.utcoffset() == timedelta(0), value
    if expected is not None:
        assert value == expected, (value, expected)
    return value


def is_id(value):
    assert isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value), value


def is_cents(value, expected):
    assert type(value) is int and value == expected, value
