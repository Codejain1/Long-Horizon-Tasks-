from datetime import datetime, timezone

import pytest

import boxoffice


def test_events():
    e = boxoffice.create_event("Raga Night", "Blue Hall", datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc), 200)
    assert boxoffice.get_event(e)["starts_at"] == "2026-05-01T14:30:00Z"
    with pytest.raises(boxoffice.BoxOfficeError):
        boxoffice.create_event("x", "y", datetime(2026, 5, 1), 10)
