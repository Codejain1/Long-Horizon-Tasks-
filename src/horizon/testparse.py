"""Detect test commands and parse pass/fail counts from their output.

Only counts and failing test ids leave this module: raw output is never stored
(PROJECT.md §12). Test ids are names, used to tell regressions from failures that
already existed when the task started (PROJECT.md §9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

TEST_COMMAND = re.compile(
    r"""(^|[\s;&|(/])(
        pytest | py\.test | python[\d.]*\s+-m\s+(pytest|unittest) | tox | nox |
        (npm|pnpm|yarn|bun)\s+(run\s+)?test | npx\s+(jest|vitest|mocha) | jest | vitest | mocha |
        go\s+test | cargo\s+(test|nextest) | mvn\s+(\S+\s+)*test | gradlew?\s+(\S+\s+)*test |
        make\s+(test|check) | (bundle\s+exec\s+)?rspec | phpunit | dotnet\s+test | uv\s+run\s+pytest
    )(?=$|[\s;&|)])""",
    re.X,
)


@dataclass(frozen=True)
class TestCounts:
    __test__ = False

    runner: str
    passed: int
    failed: int
    failing: tuple[str, ...] = ()  # ids of failing tests, as far as the output shows them

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def failing_complete(self) -> bool:
        """Whether every failure was identified (output cut short by `| tail` may hide some)."""
        return len(self.failing) >= self.failed


def is_test_command(command: str) -> bool:
    return bool(TEST_COMMAND.search(command or ""))


def _num(pattern: str, text: str) -> int:
    return sum(int(m) for m in re.findall(pattern, text))


def _pytest(text: str) -> TestCounts | None:
    # "==== 2 failed, 10 passed, 1 skipped in 0.12s ====" (last summary line wins)
    lines = [ln for ln in text.splitlines()
             if re.search(r"\d+ (passed|failed|error)", ln) and re.search(r"\bin [\d.]+s", ln)]
    if not lines:
        return None
    last = lines[-1]
    passed = _num(r"(\d+) passed", last)
    failed = _num(r"(\d+) failed", last) + _num(r"(\d+) errors?\b", last)
    return TestCounts("pytest", passed, failed)


def _unittest(text: str) -> TestCounts | None:
    ran = re.findall(r"^Ran (\d+) tests? in", text, re.M)
    if not ran:
        return None
    total = int(ran[-1])
    status = re.search(r"^(OK|FAILED)(?: \(([^)]*)\))?", text, re.M)
    details = (status.group(2) or "") if status else ""
    failed = _num(r"failures=(\d+)", details) + _num(r"errors=(\d+)", details)
    skipped = _num(r"skipped=(\d+)", details)
    return TestCounts("unittest", max(0, total - failed - skipped), failed)


def _jest(text: str) -> TestCounts | None:
    # Jest: "Tests:       1 failed, 4 passed, 5 total"
    m = re.findall(r"^\s*Tests:\s+(.*\d+ total)", text, re.M)
    if m:
        line = m[-1]
        return TestCounts("jest", _num(r"(\d+) passed", line), _num(r"(\d+) failed", line))
    # Vitest: "Tests  1 failed | 4 passed (5)"
    m = re.findall(r"^\s*Tests\s+(.*\(\d+\))\s*$", text, re.M)
    if m:
        line = m[-1]
        return TestCounts("vitest", _num(r"(\d+) passed", line), _num(r"(\d+) failed", line))
    return None


def _cargo(text: str) -> TestCounts | None:
    results = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed", text)
    if not results:
        return None
    return TestCounts("cargo", sum(int(p) for p, _ in results), sum(int(f) for _, f in results))


def _go(text: str) -> TestCounts | None:
    passed = len(re.findall(r"^\s*--- PASS:", text, re.M))
    failed = len(re.findall(r"^\s*--- FAIL:", text, re.M))
    if passed or failed:
        return TestCounts("go", passed, failed)
    # Without -v, go only prints package lines: count packages.
    ok = len(re.findall(r"^ok\s+\S+", text, re.M))
    bad = len(re.findall(r"^FAIL\s+\S+\s", text, re.M))
    return TestCounts("go", ok, bad) if ok or bad else None


def _rspec(text: str) -> TestCounts | None:
    m = re.findall(r"(\d+) examples?, (\d+) failures?", text)
    if not m:
        return None
    total, failed = map(int, m[-1])
    return TestCounts("rspec", total - failed, failed)


_PARSERS = (_pytest, _cargo, _jest, _rspec, _unittest, _go)

MAX_FAILING_IDS = 500
_FAILING_IDS = {
    "pytest": re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M),  # short test summary info
    "unittest": re.compile(r"^(?:FAIL|ERROR): (\w+) \(([\w.]+)\)", re.M),
    "go": re.compile(r"^\s*--- FAIL: (\S+)", re.M),
    "cargo": re.compile(r"^test (\S+) \.\.\. FAILED", re.M),
    "jest": re.compile(r"^\s*● (.+?)\s*$", re.M),
    "rspec": re.compile(r"^rspec (\./\S+)", re.M),
}


def failing_ids(runner: str, text: str) -> tuple[str, ...]:
    pattern = _FAILING_IDS.get(runner)
    if pattern is None:
        return ()
    ids = []
    for m in pattern.finditer(text):
        test_id = f"{m.group(2)}.{m.group(1)}" if runner == "unittest" else m.group(1)
        if test_id not in ids:
            ids.append(test_id)
    return tuple(ids[:MAX_FAILING_IDS])


def parse_test_output(text: str) -> TestCounts | None:
    for parser in _PARSERS:
        counts = parser(text or "")
        if counts is not None and counts.total > 0:
            return replace(counts, failing=failing_ids(counts.runner, text or "")) if counts.failed else counts
    return None
