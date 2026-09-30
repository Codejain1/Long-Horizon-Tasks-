import pytest

from horizon.testparse import is_test_command, may_run_tests, parse_test_output


@pytest.mark.parametrize("cmd", ["./ci.sh", "cd app && ./ci.sh -q", "sh scripts/test.sh", "bash ci.sh",
                                 "scripts/run_tests", "just test", "bazel test //..."])
def test_wrappers_may_run_tests(cmd):
    assert may_run_tests(cmd) and not is_test_command(cmd)


@pytest.mark.parametrize("cmd", ["ls -la", "git status", "cat tests/test_x.py", "echo testing", "grep -r failed .",
                                 "cd app && cat logs/test.log", "tail -n 50 build/ci.sh"])
def test_plain_commands_never_count(cmd):
    assert not may_run_tests(cmd)


@pytest.mark.parametrize("cmd", [
    "pytest -q", "python -m pytest tests/", "cd app && npm test", "uv run pytest -x", "go test ./...",
    "cargo test", "npx vitest run", "yarn test", "bundle exec rspec", "python3 -m unittest discover",
    "make test", "./gradlew clean test",
])
def test_detects_test_commands(cmd):
    assert is_test_command(cmd)


@pytest.mark.parametrize("cmd", ["ls -la", "git status", "pip install pytest-cov", "cat tests/test_x.py", "echo testing"])
def test_ignores_other_commands(cmd):
    assert not is_test_command(cmd)


@pytest.mark.parametrize("output,runner,passed,failed", [
    ("collected 13 items\n...\n==== 2 failed, 10 passed, 1 skipped in 0.52s ====", "pytest", 10, 2),
    ("=== 5 passed in 1.00s ===", "pytest", 5, 0),
    ("=== 1 failed, 3 passed, 2 errors in 2.1s ===", "pytest", 3, 3),
    ("....\n----------\nRan 4 tests in 0.001s\n\nOK", "unittest", 4, 0),
    ("Ran 6 tests in 0.1s\n\nFAILED (failures=1, errors=1, skipped=1)", "unittest", 3, 2),
    ("Tests:       1 failed, 4 passed, 5 total\nTime: 1s", "jest", 4, 1),
    (" Test Files  1 passed (1)\n      Tests  2 failed | 8 passed (10)", "vitest", 8, 2),
    ("test result: ok. 3 passed; 0 failed; 0 ignored\ntest result: FAILED. 1 passed; 2 failed;", "cargo", 4, 2),
    ("=== RUN TestA\n--- PASS: TestA (0.00s)\n--- FAIL: TestB (0.00s)\nFAIL", "go", 1, 1),
    ("ok  \tgithub.com/x/y\t0.01s\nFAIL\tgithub.com/x/z\t0.02s", "go", 1, 1),
    ("Finished in 0.1s\n7 examples, 2 failures", "rspec", 5, 2),
])
def test_parses_counts(output, runner, passed, failed):
    counts = parse_test_output(output)
    assert counts is not None
    assert (counts.runner, counts.passed, counts.failed) == (runner, passed, failed)


def test_unparseable_output_returns_none():
    assert parse_test_output("command not found: pytest") is None
    assert parse_test_output("") is None


@pytest.mark.parametrize("output,ids", [
    ("FAILED tests/test_a.py::test_x - AssertionError: boom\nERROR tests/test_b.py::test_y\n"
     "==== 1 failed, 3 passed, 1 error in 0.1s ====", ("tests/test_a.py::test_x", "tests/test_b.py::test_y")),
    ("FAIL: test_x (pkg.tests.TestA)\n----\nRan 2 tests in 0.1s\n\nFAILED (failures=1)", ("pkg.tests.TestA.test_x",)),
    ("--- FAIL: TestB (0.00s)\n--- PASS: TestA (0.00s)\nFAIL", ("TestB",)),
    ("test tests::b ... FAILED\ntest result: FAILED. 1 passed; 1 failed;", ("tests::b",)),
    ("  ● math › adds\n\nTests:       1 failed, 4 passed, 5 total", ("math › adds",)),
])
def test_failing_ids(output, ids):
    counts = parse_test_output(output)
    assert counts.failing == ids and counts.failing_complete


def test_failing_ids_cut_short_are_marked_incomplete():
    # e.g. `pytest -q | tail -1`: the summary survives, the FAILED lines don't.
    counts = parse_test_output("==== 2 failed, 3 passed in 0.1s ====")
    assert counts.failing == () and not counts.failing_complete
    assert parse_test_output("=== 5 passed in 1.00s ===").failing_complete
