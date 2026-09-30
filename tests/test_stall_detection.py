"""v2 Phase 2: stall detection warns a session stuck on the same failing check, and stays quiet on hard work."""

import dataclasses
import json

import pytest
from test_session_episodes import Session, pytest_output

from horizon import stall
from horizon.eventlog.store import EventStore
from horizon.stall import HEADER, Run, check, stuck, threshold

TASK = "Make create_invoice print a line on stdout for the log shipper."
STUCK = "tests/test_o.py::test_refund"


def runs(*counts, failing=(STUCK,)):
    return [Run(p, f, failing if f else ()) for p, f in counts]


# --- the rule ------------------------------------------------------------------------------------------

def test_stuck_means_the_same_check_failing_with_no_progress():
    assert stuck(runs((3, 1), (3, 1), (3, 1), (3, 1)), 4) == [STUCK]
    assert stuck(runs((3, 1), (3, 1), (3, 1)), 4) == []  # not yet
    assert stuck(runs((3, 1), (3, 1), (4, 1), (3, 1)), 4) == []  # more passing at some point: progress
    assert stuck(runs((3, 1), (3, 1), (4, 0), (3, 1)), 4) == []  # it passed in between
    assert stuck(runs((3, 1), (3, 1), (3, 1), (3, 1), failing=None), 4) == []  # no ids: never guess
    assert stuck([Run(3, 1, ("a",)), Run(3, 1, ("b",)), Run(3, 1, ("a",)), Run(3, 1, ("a",))], 4) == []
    assert stuck(runs((5, 0), (3, 1), (3, 1), (3, 1), (3, 1)), 4) == [STUCK]  # only the last n count


def test_the_threshold_follows_what_similar_sessions_needed():
    forecast = lambda p90: dataclasses.make_dataclass("F", ["failed_runs_p90"])(p90)  # noqa: E731
    assert threshold(None, 4) == 4 and threshold(forecast(None), 4) == 4
    assert threshold(forecast(1.0), 4) == 4 and threshold(forecast(3.2), 4) == 6


def test_the_warning_says_what_similar_sessions_needed():
    assert "usually passed without a failing run" in stall.warning([STUCK], 4, 0.0)
    assert "needed at most 2 failing runs" in stall.warning([STUCK], 4, 1.4)
    assert "Sessions on similar" not in stall.warning([STUCK], 4, None)
    assert stall.warning(["a", "b", "c", "d"], 4, None).startswith(f"{HEADER} a, b, c (+1 more) failed")


def test_the_forecast_is_asked_only_once_the_cheap_pattern_holds():
    asked = []
    forecasts = lambda k: asked.append(k) or (None, None)  # noqa: E731
    assert check(runs((3, 1), (3, 1), (3, 1)), 4, forecasts) is None and asked == []
    assert check(runs((3, 1), (3, 1), (3, 1), (3, 1)), 4, forecasts).startswith(HEADER) and asked == [4]


# --- through the hooks ---------------------------------------------------------------------------------

@pytest.fixture
def on(settings):
    return dataclasses.replace(settings, stall="on")


def play(settings, project, session_id, counts, prompt=TASK, end=True):
    """A session's test runs; returns what each run's hook said."""
    s = Session(settings, project, session_id)
    s.hook("session-start", source="startup")
    s.hook("user-prompt", prompt=prompt)
    said = []
    for passed, failed in counts:
        out = s.hook("post-tool-use", tool_name="Bash", tool_input={"command": "pytest -q"},
                     tool_response={"stdout": pytest_output(passed, failed)})
        said.append(json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None)
    s.hook("stop", stop_hook_active=False)
    if end:
        s.hook("session-end", reason="other")
    return said


def test_off_by_default(settings, project_dir):
    assert settings.stall == "off" and play(settings, project_dir, "s1", [(3, 1)] * 6) == [None] * 6


def test_a_stuck_session_is_warned_once(on, project_dir):
    said = play(on, project_dir, "s1", [(3, 1)] * 7)
    assert said[:3] == [None] * 3 and said[3].startswith(HEADER) and said[4:] == [None] * 3
    assert STUCK in said[3] and "BLOCKED.md" in said[3] and "last 4 test runs" in said[3]
    flagged = [e for e in EventStore(connect_(on)).session(on.team_id, project_dir, "s1") if e.data.get("stall_warning")]
    assert len(flagged) == 1 and flagged[0].data["injected_chars"] == len(said[3])


def connect_(settings):
    from horizon.db import connect

    return connect(settings.db_url)


def test_hard_work_that_makes_progress_is_never_warned(on, project_dir):
    """Ten failing runs, but more tests pass as it goes: the hard control ticket's shape."""
    counts = [(1, 5), (1, 5), (2, 4), (2, 4), (2, 4), (3, 3), (3, 3), (3, 3), (4, 2), (5, 1), (6, 0)]
    assert play(on, project_dir, "s1", counts) == [None] * len(counts)


def test_a_history_of_hard_sessions_raises_the_bar(on, project_dir):
    """Similar sessions here needed 5 failing runs before going green, so 4 stuck runs is normal: the warning
    waits for 5 + 2."""
    for i in range(3):
        play(on, project_dir, f"past{i}", [(3, 1)] * 5 + [(4, 0)])
    said = play(on, project_dir, "now", [(3, 1)] * 8)
    assert said[:6] == [None] * 6 and said[6].startswith(HEADER) and "last 7 test runs" in said[6]
    assert "needed at most 5 failing runs" in said[6]


def test_quiet_when_similar_sessions_that_got_this_far_ended_green(on, project_dir):
    """Most similar sessions went cleanly (the 90th percentile is 0), but the two that got 4 failing runs deep
    still finished: that's what hard work here looks like."""
    for i in range(25):
        play(on, project_dir, f"ok{i}", [(4, 0)])
    for i in range(2):
        play(on, project_dir, f"long{i}", [(3, 1)] * 10 + [(4, 0)])
    assert play(on, project_dir, "now", [(3, 1)] * 5) == [None] * 5


def test_the_embedder_loads_only_once_the_pattern_holds(on, project_dir, monkeypatch):
    from horizon.memrouter import embedding

    play(on, project_dir, "past", [(3, 1), (4, 0)])
    loads, real = [], embedding.make_embedder
    monkeypatch.setattr(embedding, "make_embedder", lambda *a: loads.append(1) or real(*a))
    s = Session(on, project_dir, "now")
    s.hook("session-start", source="startup")
    s.hook("user-prompt", prompt=TASK)
    for i in range(4):
        s.hook("post-tool-use", tool_name="Bash", tool_input={"command": "pytest -q"},
               tool_response={"stdout": pytest_output(3, 1)})
        assert loads == ([] if i < 3 else [1])


def test_a_failing_forecast_still_warns_at_the_floor(on, project_dir, monkeypatch):
    from horizon.eventlog import forecast

    play(on, project_dir, "past", [(3, 1), (4, 0)])
    monkeypatch.setattr(forecast.SessionWorldModel, "predict", lambda *a, **k: 1 / 0)
    said = play(on, project_dir, "now", [(3, 1)] * 4)
    assert said[3].startswith(HEADER) and "needed at most" not in said[3]


def test_hosted_installs_have_no_stall_check():
    from horizon.hooks import Remote

    assert Remote("https://h.example", "k").stall_check("prj_a", "s1") is None
    assert stall.MAX_TESTS == 3
