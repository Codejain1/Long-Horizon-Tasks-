"""Small text helpers. Several are unfinished or buggy on purpose (see TASKS.md)."""

import re


def slugify(text: str) -> str:
    """Lowercase, ASCII-only, words joined by single hyphens: "Hello, World!" -> "hello-world"."""
    raise NotImplementedError


def parse_duration(text: str) -> int:
    """Parse durations like "45s", "10m", "2h" or "1h30m" into seconds."""
    match = re.fullmatch(r"(\d+)([hms])", text.strip())
    if not match:
        raise ValueError(f"bad duration: {text!r}")
    value, unit = int(match.group(1)), match.group(2)
    return value * {"h": 3600, "m": 60, "s": 1}[unit]


def word_count(text: str) -> int:
    """Number of words in the text."""
    return len(text.split(" "))


def chunk(items: list, size: int) -> list[list]:
    """Split items into lists of at most `size` elements."""
    return [items[i : i + size] for i in range(0, len(items), size)]


SMALL_WORDS = {"a", "an", "the", "and", "but", "or", "of", "in", "on", "at", "to", "for", "by"}


def title_case(text: str) -> str:
    """Capitalise each word, except SMALL_WORDS that are neither first nor last:
    "the lord of the rings" -> "The Lord of the Rings", "don't stop" -> "Don't Stop"."""
    raise NotImplementedError
