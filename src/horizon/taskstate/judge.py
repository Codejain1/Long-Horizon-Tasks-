"""Judge a test run against the task's baseline (PROJECT.md §9).

Tests that were already failing when the task started are not the task's fault:
they are excluded from the pass rate and reported separately. A rollback is
triggered only by regressions (tests failing now that weren't failing at the
baseline). Target tests, the ones the task must fix, always count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from horizon.models import TaskState, TestResults

REPORT_IDS = 20  # ids listed per category in the outcome; the counts are always complete
_TEST_PATH = re.compile(r"[\w./-]*\btests?[\w./-]*\.(?:py|js|jsx|ts|tsx|go|rb|rs|java|php)\b"
                        r"|[\w./-]*_test\.(?:go|py)\b|[\w./-]*\.(?:test|spec)\.[jt]sx?\b")


def targets_from_goal(goal: str) -> list[str]:
    """Test files named in the user's request, e.g. "so tests/test_slugify.py passes"."""
    return list(dict.fromkeys(_TEST_PATH.findall(goal)))


def is_target(test_id: str, targets: list[str]) -> bool:
    return any(t and t in test_id for t in targets)


@dataclass
class Judgement:
    success: float
    failed: bool  # triggers the rollback rules
    method: str  # "baseline": judged on regressions; "absolute": pass rate vs the threshold
    regressions: list[str] = field(default_factory=list)
    pre_existing: list[str] = field(default_factory=list)
    target_failing: list[str] = field(default_factory=list)

    def report(self) -> dict:
        return {
            "method": self.method,
            "regressions": self.regressions[:REPORT_IDS],
            "regression_count": len(self.regressions),
            "pre_existing_failures": self.pre_existing[:REPORT_IDS],
            "pre_existing_count": len(self.pre_existing),
            "target_tests_failing": self.target_failing[:REPORT_IDS],
        }


def judge(task: TaskState, tests: TestResults | None, failing: list[str] | None, success: float,
          rollback_below: float) -> Judgement:
    """`failing` is the complete list of failing test ids, or None when it isn't known."""
    if tests is None or failing is None or task.baseline != "captured":
        # No per-test view (host-reported counts, output cut short, or no baseline run): absolute rule.
        return Judgement(success=success, failed=success < rollback_below, method="absolute")
    baseline = set(task.baseline_failing)
    pre = [t for t in failing if t in baseline and not is_target(t, task.target_tests)]
    regressions = [t for t in failing if t not in baseline]
    counted = tests.total - len(pre)
    return Judgement(
        success=(tests.passed / counted) if counted else 1.0,
        failed=bool(regressions),
        method="baseline",
        regressions=regressions,
        pre_existing=pre,
        target_failing=[t for t in failing if is_target(t, task.target_tests)],
    )
