"""Stall detection (v2 Phase 2, `HORIZON_STALL=on`): tell an unattended agent it is stuck before it burns the
budget on variations of an attempt that can't work.

A session is stuck when the same check has failed in each of its last N test runs with no more tests passing
than at the start of those runs. A hard but solvable task fails too, but it makes progress, so progress (more
passing) resets nothing but keeps the warning away. N comes from the session forecast (track C): at least
`stall_min_runs`, and 2 more than the 90th percentile of failing runs similar sessions here needed. And when
similar sessions that got this far mostly ended green, it stays quiet: this is what hard work here looks like.
It speaks once per session, and suggests stopping only for causes the agent may not change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

HEADER = "Horizon stall check:"
MAX_TESTS = 3
QUIET_SUCCESS = 0.8  # similar sessions that got this far and ended green this often: no warning
QUIET_SAMPLES = 2.0


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


def threshold(forecast, floor: int) -> int:
    p90 = forecast.failed_runs_p90 if forecast else None
    return max(floor, math.ceil(p90) + 2) if p90 is not None else floor


def warning(tests: list[str], n: int, p90: float | None) -> str:
    shown = ", ".join(tests[:MAX_TESTS]) + (f" (+{len(tests) - MAX_TESTS} more)" if len(tests) > MAX_TESTS else "")
    most = math.ceil(p90) if p90 is not None else None
    usual = ("" if most is None else " Sessions on similar tasks here usually passed without a failing run."
             if most == 0 else f" Sessions on similar tasks here needed at most {most} failing runs.")
    return (f"{HEADER} {shown} failed in each of your last {n} test runs, with no more tests passing.{usual} "
            "Before another variation, find out why it fails. If the cause is something you may not change (a "
            "policy or platform test, a missing system tool or service, a rule of the task), stop and report the "
            "blocker the way the task asks (for example in BLOCKED.md) instead of working around it. Otherwise, "
            "change approach rather than retrying.")


def check(runs: list[Run], floor: int, forecasts) -> str | None:
    """The warning for this session's test runs so far, or None. `forecasts(k)` returns the fresh forecast and the
    one for sessions that got k failing runs; it is called only once the cheap pattern already holds."""
    if not stuck(runs, floor):
        return None
    k = sum(r.failed > 0 for r in runs)
    fresh, so_far = forecasts(k)
    n = threshold(fresh, floor)
    tests = stuck(runs, n)
    if not tests:
        return None
    if so_far and so_far.samples >= QUIET_SAMPLES and so_far.success >= QUIET_SUCCESS:
        return None
    return warning(tests, n, fresh.failed_runs_p90 if fresh else None)
