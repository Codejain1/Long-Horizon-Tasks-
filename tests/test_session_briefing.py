"""v2 Phase 1 `session_memory`: a lean task starts with what similar past sessions taught (Q65)."""

import dataclasses
import json

import pytest
from test_session_episodes import TEAM, Session, pytest_output

from horizon.eventlog import briefing
from horizon.eventlog.briefing import HEADER, MAX_CHARS, describe, past_sessions, trace
from horizon.hooks import injected_chars

REFUNDS = "Add partial refunds to the orders module."


@pytest.fixture
def on(settings):
    # the tests' hash embedder puts related tasks at 0.56+ and unrelated ones at ~0.39
    return dataclasses.replace(settings, session_memory="on", session_memory_min_similarity=0.5)


def work(settings, project, session_id, prompt, files=("shop/orders.py",), runs=((4, 0),), end=True):
    """One session: a prompt, some edits, test runs; returns what the first prompt's hook said."""
    s = Session(settings, project, session_id)
    s.hook("session-start", source="startup")
    said = s.hook("user-prompt", prompt=prompt)
    for f in files:
        s.hook("post-tool-use", tool_name="Edit", tool_input={"file_path": f"{project}/{f}"}, tool_response={})
    for passed, failed in runs:
        s.hook("post-tool-use", tool_name="Bash", tool_input={"command": "pytest -q"},
               tool_response={"stdout": pytest_output(passed, failed)})
    s.hook("stop", stop_hook_active=False)
    if end:
        s.hook("session-end", reason="other")
    return json.loads(said)["hookSpecificOutput"]["additionalContext"] if said else ""


def test_off_by_default_nothing_but_the_task_id(settings, project_dir):
    """Principle 5: nothing is on until it passes the evaluation gate; the evals' candidate arm turns it on."""
    work(settings, project_dir, "s1", REFUNDS, runs=[(3, 1), (4, 0)])
    said = work(settings, project_dir, "s2", REFUNDS)
    assert settings.session_memory == "off" and said.startswith("Horizon task id:") and HEADER not in said


def test_a_similar_task_starts_with_where_the_last_one_went_and_what_tripped_it(on, project_dir):
    work(on, project_dir, "s1", REFUNDS, files=["shop/orders.py", "shop/refunds.py"], runs=[(3, 1), (4, 0)])
    said = work(on, project_dir, "s2", REFUNDS)
    assert said.startswith("Horizon task id:")
    brief = said.split("\n\n", 1)[1]
    assert brief.splitlines() == [
        HEADER,
        (f'- "{REFUNDS}": ended with 4/4 tests passing after 2 test runs (1 failing); changed shop/orders.py, '
         "shop/refunds.py; failed along the way: tests/test_o.py::test_refund.")]


def test_a_session_that_ended_badly_says_what_was_still_failing(on, project_dir):
    work(on, project_dir, "s1", REFUNDS, runs=[(3, 1)])
    said = work(on, project_dir, "s2", REFUNDS)
    assert "ended with 3/4 tests passing on the first test run" not in said  # it failed: not a first-try pass
    assert "after 1 test runs (1 failing)" in said and "still failing at the end: tests/test_o.py::test_refund" in said


def test_an_unrelated_task_or_a_new_project_gets_nothing(on, project_dir, tmp_path):
    work(on, project_dir, "s1", REFUNDS, runs=[(4, 0)])
    assert HEADER not in work(on, project_dir, "s2", "Write the onboarding guide for new contributors.")
    other = tmp_path / "other"
    other.mkdir()
    assert HEADER not in work(on, str(other), "s3", REFUNDS)  # projects are walls for session history


def test_no_history_never_loads_memory(on, project_dir, log):
    assert past_sessions(log, TEAM, project_dir, REFUNDS, lambda: pytest.fail("the embedder was loaded")) is None


def test_only_the_first_prompt_of_a_task_gets_it(on, project_dir):
    work(on, project_dir, "s1", REFUNDS, runs=[(4, 0)])
    s = Session(on, project_dir, "s2")
    s.hook("session-start", source="startup")
    assert HEADER in s.hook("user-prompt", prompt=REFUNDS)
    assert s.hook("user-prompt", prompt=REFUNDS) == ""  # a progress note: no repeat, no cost


def test_it_stays_within_its_budget_however_much_memory_holds(on, project_dir):
    for i in range(6):
        files = [f"shop/module_{j}/very_long_file_name_{i}_{j}.py" for j in range(12)]
        work(on, project_dir, f"s{i}", REFUNDS, files=files, runs=[(3, 1), (2, 2), (4, 0)])
    said = work(on, project_dir, "new", REFUNDS)
    brief = said.split("\n\n", 1)[1]
    assert len(brief) <= MAX_CHARS and 1 <= brief.count('\n- "') <= 3 and "(+6 more)" in brief
    assert injected_chars({"hookSpecificOutput": {"additionalContext": said}}) == len(said)


def test_sessions_that_went_the_same_way_are_one_line(on, project_dir):
    for i in range(3):
        work(on, project_dir, f"s{i}", REFUNDS, runs=[(4, 0)])
    brief = work(on, project_dir, "new", REFUNDS).split("\n\n", 1)[1]
    assert brief.splitlines()[1:] == [(f'- "{REFUNDS}": ended with 4/4 tests passing on the first test run; changed '
                                       "shop/orders.py. (3 sessions went this way)")]


def test_memory_failing_never_stops_the_task_starting(on, project_dir, monkeypatch):
    import horizon.eventlog.episodes as mod

    work(on, project_dir, "s1", REFUNDS, runs=[(4, 0)])
    monkeypatch.setattr(mod, "local_memory", lambda *a: 1 / 0)
    said = work(on, project_dir, "s2", REFUNDS)
    assert said.startswith("Horizon task id:") and HEADER not in said


def test_no_code_reaches_the_briefing(on, project_dir):
    code = "```python\ndef leak_marker_fn(order):\n    return order.total * 0.9\n```"
    work(on, project_dir, "s1", f"{REFUNDS}\n{code}", runs=[(4, 0)])
    said = work(on, project_dir, "s2", REFUNDS)
    assert HEADER in said and "leak_marker_fn" not in said


def test_a_shared_preamble_is_cut_so_each_task_says_what_it_was(on, project_dir):
    """Eval and team prompts often open with the same standing instructions; the label shows the difference."""
    preamble = "You are working through the backlog on your own, so make reasonable choices. "
    work(on, project_dir, "s1", preamble + "Ticket #1: add partial refunds to orders.", runs=[(4, 0)])
    brief = work(on, project_dir, "s2", preamble + "Ticket #2: add refunds to the CSV export.")
    assert '- "…#1: add partial refunds to orders.": ended' in brief  # cut at the first word that differs
    assert briefing.distinct("Short task.", "Short task, again.") == "Short task."  # nothing long shared
    assert briefing.distinct(preamble + "x", preamble + "y").startswith("…")


def test_trace_and_describe_are_pure_summaries_of_the_log(on, project_dir, log):
    work(on, project_dir, "s1", REFUNDS, files=["a.py", "b.py", "a.py"], runs=[(1, 1), (2, 0)])
    [row] = log.episodes(TEAM)
    events = log.segment_events(TEAM, log.segment_of(TEAM, row["episode_id"]))
    t = trace(events)
    assert (t.files, t.runs, t.failed_runs, t.tripped, t.still_failing) == (
        ["a.py", "b.py"], 2, 1, ["tests/test_o.py::test_refund"], [])
    assert describe("x" * 500, t).startswith('- "' + "x" * 139 + "…")
    assert describe("Talk only.", trace([])) == '- "Talk only."'
