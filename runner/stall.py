"""Stall detection for the runner's watcher: a step is stuck when the same check has failed in each of its last N
test runs with no more tests passing than at the start of those runs. A hard but solvable step fails too, but it
makes progress, so more passing tests keep it from counting as stuck.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Run:
    passed: int
    failed: int
    failing: tuple[str, ...] | None  # ids, when the runner's output gave them


def stuck(runs: list[Run], n: int) -> list[str]:
    """The checks that failed in each of the last `n` runs with no progress (sorted), or []."""
    if n < 1 or len(runs) < n:
        return []
    last = runs[-n:]
    if any(r.failed == 0 or not r.failing for r in last):
        return []
    if max(r.passed for r in last) > last[0].passed:  # more tests passing than when the streak began: progress
        return []
    return sorted(set.intersection(*(set(r.failing) for r in last)))
