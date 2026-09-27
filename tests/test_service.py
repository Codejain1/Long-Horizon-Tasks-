import pytest

from horizon.models import Condition, TestCapture
from horizon.service import Platform, ToolInputError


def test_start_task_and_recall_return_task_state(platform):
    goal = "Add rate limiting to the /login endpoint (max 5/min per IP)."
    started = platform.start_task(goal, constraints=["no redis"], plan=["add middleware", "test"])
    task_id = started["task_id"]
    assert started["task_state"]["goal"] == goal

    ctx = platform.recall_context(task_id, "choose a rate limiting approach")
    assert ctx["task_state"]["goal"] == goal
    assert ctx["task_state"]["constraints"] == ["no redis"]
    assert ctx["memory_status"] == "ok"
    assert ctx["recall_id"].startswith("rc_")
    assert ctx["memories"] == []


def test_full_loop_records_episode_and_updates_task(platform, memrouter):
    task_id = platform.start_task("Add rate limiting")["task_id"]
    ctx = platform.recall_context(task_id, "choose a rate limiting approach for flask")
    out = platform.record_outcome(
        task_id, "choose a rate limiting approach for flask", "flask-limiter",
        tests_passed=9, tests_failed=1, predicted_success=0.8, recall_id=ctx["recall_id"],
        reason="well maintained", alternatives=["custom middleware"],
        conditions=[Condition(key="framework", value="flask")],
    )
    assert out["memory_status"] == "ok"
    assert out["actual_success"] == 0.9
    assert out["test_results_source"] == "host"
    assert out["surprise"] == pytest.approx(0.1)  # 0.9 - 0.8, no efficiency metrics
    assert out["low_confidence"] is False

    ep = memrouter.store.get(out["episode_id"], "local")
    assert ep.recall_id == ctx["recall_id"]
    assert ep.alternatives[0].label == "custom middleware"
    assert ep.actual.test_results.total == 10

    state = platform.recall_context(task_id, "next step")["task_state"]
    assert state["decisions"][-1]["chosen"] == "flask-limiter"
    assert state["decisions"][-1]["reason"] == "well maintained"
    assert "tests 9/10 passed" in state["recent_progress"][-1]

    # A later, similar decision recalls the outcome.
    other = platform.start_task("Throttle the signup endpoint")["task_id"]
    mem = platform.recall_context(other, "choose a rate limiting approach for flask")["memories"]
    assert mem and mem[0]["chosen"] == "flask-limiter"


def test_hook_captured_tests_override_self_report(platform, task_store, project_dir):
    task_id = platform.start_task("fix the parser")["task_id"]
    platform.recall_context(task_id, "fix tokenizer bug")  # runs before the first recall are the baseline
    task_store.add_capture(TestCapture(cwd=project_dir, command="pytest", runner="pytest",
                                       passed=2, failed=3, total=5))
    out = platform.record_outcome(task_id, "fix tokenizer bug", "rewrite regex", tests_passed=5, tests_failed=0,
                                  signal_type="human")
    assert out["test_results_source"] == "hook"
    assert out["actual_success"] == 0.4
    assert task_store.pending_captures(project_dir) == []


def test_captures_from_other_dirs_or_before_the_task_are_ignored(platform, task_store, project_dir):
    from datetime import timedelta

    from horizon.models import now

    task_store.add_capture(TestCapture(cwd=project_dir, command="pytest", runner="pytest", passed=0, failed=1,
                                       total=1, created_at=now() - timedelta(days=1)))
    task_store.add_capture(TestCapture(cwd="/elsewhere", command="pytest", runner="pytest", passed=0, failed=1,
                                       total=1))
    task_id = platform.start_task("x")["task_id"]
    out = platform.record_outcome(task_id, "s", "c", tests_passed=1, tests_failed=0)
    assert out["test_results_source"] == "host"


def test_record_requires_a_result(platform):
    task_id = platform.start_task("x")["task_id"]
    with pytest.raises(ToolInputError, match="tests_passed"):
        platform.record_outcome(task_id, "s", "c")
    out = platform.record_outcome(task_id, "s", "c", success=1.0, signal_type="implicit")
    assert out["actual_success"] == 1.0 and out["test_results_source"] is None


def test_unknown_task_and_empty_goal_are_rejected(platform):
    with pytest.raises(ToolInputError, match="start_task"):
        platform.recall_context("task_missing", "s")
    with pytest.raises(ToolInputError):
        platform.start_task("   ")


def test_task_complete_and_open_issues(platform):
    task_id = platform.start_task("x", open_issues=["unclear spec"])["task_id"]
    out = platform.record_outcome(task_id, "s", "c", success=1.0, open_issues=[], task_complete=True)
    assert out["task_status"] == "completed"
    assert platform.tasks.get(task_id, "local").open_issues == []


def test_task_state_survives_memrouter_failure(settings, task_store, project_dir):
    def broken():
        raise ConnectionError("memrouter down")

    platform = Platform(settings, task_store, broken, cwd=project_dir)
    task_id = platform.start_task("keep working without memory")["task_id"]

    ctx = platform.recall_context(task_id, "choose an approach")
    assert ctx["memory_status"] == "unavailable"
    assert ctx["task_state"]["goal"] == "keep working without memory"
    assert ctx["memories"] == []

    task_store.add_capture(TestCapture(cwd=project_dir, command="pytest", runner="pytest", passed=1, failed=0,
                                       total=1))
    out = platform.record_outcome(task_id, "choose an approach", "option A", tests_passed=1, tests_failed=0)
    assert out["memory_status"] == "unavailable" and out["episode_id"] is None
    assert platform.tasks.get(task_id, "local").decisions[0].chosen == "option A"
    assert task_store.pending_captures(project_dir) == []  # consumed, so the Stop hook won't nag


def test_memrouter_recovers_after_transient_failure(settings, task_store, memrouter, project_dir):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("not yet")
        return memrouter

    platform = Platform(settings, task_store, flaky, cwd=project_dir)
    task_id = platform.start_task("x")["task_id"]
    assert platform.recall_context(task_id, "s")["memory_status"] == "unavailable"
    assert platform.recall_context(task_id, "s")["memory_status"] == "ok"


def test_every_call_is_logged(platform, task_store):
    task_id = platform.start_task("x")["task_id"]
    platform.recall_context(task_id, "s")
    platform.record_outcome(task_id, "s", "c", success=1.0)
    with pytest.raises(ToolInputError):
        platform.recall_context("task_nope", "s")
    calls = task_store.stats()["tool_calls"]
    assert calls["start_task"] == {"calls": 1, "errors": 0}
    assert calls["recall_context"] == {"calls": 2, "errors": 1}
    assert calls["record_outcome"] == {"calls": 1, "errors": 0}
