import pytest

from textkit.core import parse_duration


@pytest.mark.parametrize("text,seconds", [("45s", 45), ("10m", 600), ("2h", 7200), ("1h30m", 5400),
                                          ("1h2m3s", 3723), (" 5m ", 300)])
def test_parses(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "10", "5x", "m10", "1h1h"])
def test_rejects(text):
    with pytest.raises(ValueError):
        parse_duration(text)
