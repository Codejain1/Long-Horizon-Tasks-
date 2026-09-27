"""Pre-existing failures (PROJECT.md §9, open questions 29/35): judge outcomes on regressions against a
baseline taken at the start of the task, not on the absolute pass rate."""

import json

import pytest

from horizon.hooks import run_hook
from horizon.taskstate.judge import targets_from_goal

GOAL = "Implement title_case in textkit/core.py so that tests/test_title_case.py passes."
SLUG = "tests/test_slugify.py::test_basic"  # another task's unfinished stub: failing before we start
T1, T2 = "tests/test_title_case.py::test_small_words", "tests/test_title_case.py::test_apostrophes"
CHUNK = "tests/test_chunk.py::test_empty"  # passing at the start


@pytest.fixture(autouse=True)
def no_project_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def pytest_run(settings, cwd, failing, passed, command="python -m pytest -q"):
    """What the PostToolUse hook sees for one pytest run."""
    lines = [f"FAILED {t} - AssertionError" for t in failing]
    lines.append(f"==== {len(failing)} failed, {passed} passed in 0.1s ====" if failing
                 else f"==== {passed} passed in 0.1s ====")
    payload = {"session_id": "s1", "cwd": cwd, "tool_name": "Bash", "tool_input": {"command": command},
               "tool_response": {"stdout": "\n".join(lines), "stderr": ""}}
    run_hook("post-tool-use", json.dumps(payload), settings)


def start_with_baseline(platform, settings, project_dir, failing=(SLUG, T1, T2), passed=7):
    task_id = platform.start_task(GOAL)["task_id"]
    pytest_run(settings, project_dir, failing, passed)  # the full suite, before any edit
    ctx = platform.recall_context(task_id, "implement title_case")
    return task_id, ctx


def test_targets_come_from_the_goal_and_the_host():
    assert targets_from_goal(GOAL) == ["tests/test_title_case.py"]
    assert targets_from_goal("Fix src/app.spec.ts and pkg/x_test.go") == ["src/app.spec.ts", "pkg/x_test.go"]
    assert targets_from_goal("Make the button blue") == []


def test_baseline_is_the_run_before_the_first_recall(platform, settings, project_dir, task_store):
    task_id, ctx = start_with_baseline(platform, settings, project_dir)
    baseline = ctx["task_state"]["baseline"]
    assert baseline == {"status": "captured", "failing_at_start": 3, "target_tests": ["tests/test_title_case.py"]}
    assert platform.tasks.get(task_id, "local").baseline_failing == [SLUG, T1, T2]
    # The baseline run is not an outcome: nothing pending for record_outcome or the Stop hook.
    assert task_store.pending_captures(project_dir) == []


def test_whole_suite_with_other_failures_is_not_a_regression(platform, settings, project_dir):
    """The demo case: Claude runs the whole suite, where another task's stub still fails."""
    task_id, ctx = start_with_baseline(platform, settings, project_dir)
    pytest_run(settings, project_dir, [SLUG], passed=9)
    out = platform.record_outcome(task_id, "implement title_case", "word-by-word", recall_id=ctx["recall_id"])
    j = out["test_judgement"]
    assert out["actual_success"] == 1.0  # 9 of the 9 tests that count
    assert out["rollback"] is None
    assert j["method"] == "baseline" and j["pre_existing_failures"] == [SLUG] and j["regression_count"] == 0
    assert "1 already failing at start" in platform.tasks.get(task_id, "local").progress[-1].note


def test_target_tests_still_failing_count_but_do_not_roll_back(platform, settings, project_dir):
    task_id, _ = start_with_baseline(platform, settings, project_dir)
    pytest_run(settings, project_dir, [SLUG, T2], passed=8)  # str.title(): one target test still fails
    out = platform.record_outcome(task_id, "s", "str.title()")
    assert out["actual_success"] == pytest.approx(8 / 9, abs=1e-3)  # SLUG excluded; the target failure counts
    assert out["test_judgement"]["target_tests_failing"] == [T2]
    assert out["rollback"] is None  # nothing that worked before broke


def test_a_regression_triggers_the_rollback(platform, settings, project_dir):
    task_id, _ = start_with_baseline(platform, settings, project_dir)
    pytest_run(settings, project_dir, [SLUG, CHUNK], passed=8)  # chunk passed at the start, fails now
    out = platform.record_outcome(task_id, "s", "rewrote the shared helper")
    assert out["rollback"]["action"] == "rollback"
    assert out["test_judgement"]["regressions"] == [CHUNK]
    assert CHUNK in out["rollback"]["failure_reason"]
    assert out["actual_success"] == pytest.approx(8 / 9, abs=1e-3)


def test_without_a_baseline_run_the_absolute_rule_applies(platform, settings, project_dir):
    task_id = platform.start_task(GOAL)["task_id"]
    ctx = platform.recall_context(task_id, "s")  # no test run before the first recall
    assert ctx["task_state"]["baseline"]["status"] == "missing"
    pytest_run(settings, project_dir, [SLUG], passed=9)
    out = platform.record_outcome(task_id, "s", "c")
    assert out["test_judgement"]["method"] == "absolute"
    assert out["actual_success"] == 0.9 and out["rollback"]["action"] == "rollback"


def test_failures_the_output_did_not_name_fall_back_to_the_absolute_rule(platform, settings, project_dir):
    task_id, _ = start_with_baseline(platform, settings, project_dir)
    payload = {"session_id": "s1", "cwd": project_dir, "tool_name": "Bash",
               "tool_input": {"command": "python -m pytest -q | tail -1"},
               "tool_response": {"stdout": "==== 1 failed, 9 passed in 0.1s ====", "stderr": ""}}
    run_hook("post-tool-use", json.dumps(payload), settings)
    out = platform.record_outcome(task_id, "s", "c")
    assert out["test_judgement"]["method"] == "absolute" and out["rollback"]["action"] == "rollback"


def test_declared_target_tests_are_kept(platform):
    started = platform.start_task("Fix the parser", target_tests=["tests/test_parser.py::test_dates"])
    assert started["task_state"]["baseline"]["target_tests"] == ["tests/test_parser.py::test_dates"]
    assert "full test suite" in started["next"]


def test_recording_the_baseline_run_before_any_recall_takes_it_as_the_baseline(platform, settings, project_dir):
    """Seen with Sonnet: it ran the suite after start_task, then called record_outcome on that run."""
    task_id = platform.start_task(GOAL)["task_id"]
    pytest_run(settings, project_dir, [SLUG, T1, T2], passed=7)
    out = platform.record_outcome(task_id, "baseline", "none yet")
    assert out["recorded"] is False and out["baseline"] == {"status": "captured", "failing_at_start": 3}
    task = platform.tasks.get(task_id, "local")
    assert task.decisions == [] and task.attempts == 0  # no outcome, no rollback

    ctx = platform.recall_context(task_id, "implement title_case")
    assert ctx["task_state"]["baseline"]["status"] == "captured"
    pytest_run(settings, project_dir, [SLUG], passed=9)
    out = platform.record_outcome(task_id, "s", "c", recall_id=ctx["recall_id"])
    assert out["rollback"] is None and out["test_judgement"]["pre_existing_failures"] == [SLUG]


def test_record_outcome_without_tests_before_a_recall_still_records(platform):
    task_id = platform.start_task("x")["task_id"]
    out = platform.record_outcome(task_id, "s", "c", success=1.0)
    assert out["episode_id"] and platform.tasks.get(task_id, "local").baseline == "missing"
