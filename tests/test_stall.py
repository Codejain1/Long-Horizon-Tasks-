"""The watcher's stall rule: the same check failing with no progress."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "runner"))
from stall import Run, stuck  # noqa: E402

STUCK = "tests/test_o.py::test_refund"


def runs(*counts, failing=(STUCK,)):
    return [Run(p, f, failing if f else ()) for p, f in counts]


def test_stuck_means_the_same_check_failing_with_no_progress():
    assert stuck(runs((3, 1), (3, 1), (3, 1), (3, 1)), 4) == [STUCK]
    assert stuck(runs((3, 1), (3, 1), (3, 1)), 4) == []  # not yet
    assert stuck(runs((3, 1), (3, 1), (4, 1), (3, 1)), 4) == []  # more passing at some point: progress
    assert stuck(runs((3, 1), (3, 1), (4, 0), (3, 1)), 4) == []  # it passed in between
    assert stuck(runs((3, 1), (3, 1), (3, 1), (3, 1), failing=None), 4) == []  # no ids: never guess
    assert stuck([Run(3, 1, ("a",)), Run(3, 1, ("b",)), Run(3, 1, ("a",)), Run(3, 1, ("a",))], 4) == []
    assert stuck(runs((5, 0), (3, 1), (3, 1), (3, 1), (3, 1)), 4) == [STUCK]  # only the last n count
