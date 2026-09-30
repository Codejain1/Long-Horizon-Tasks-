"""v2 Phase 1 track A: session episodes written into the memrouter from the event log, with no agent call."""

import dataclasses
import json
from datetime import timedelta

import pytest

from horizon import cli
from horizon.db import connect
from horizon.eventlog.episodes import draft_episode
from horizon.hooks import run_hook
from horizon.memrouter.router import MemRouter
from horizon.memrouter.store import EpisodeStore

TEAM = "local"
TRANSCRIPT = [
    {"type": "user", "message": {"role": "user", "content": "go"}, "timestamp": "2026-09-30T10:00:00Z"},
    {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 400, "output_tokens": 80}},
     "timestamp": "2026-09-30T10:00:09Z"},
]


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in TRANSCRIPT))
    return str(path)


def pytest_output(passed, failed):
    return (f"FAILED tests/test_o.py::test_refund - AssertionError\n= {failed} failed, {passed} passed in 0.1s ="
            if failed else f"= {passed} passed in 0.1s =")


class Session:
    """Fires the installed hooks for one session the way Claude Code does."""

    def __init__(self, settings, project, session_id, profile="lean", transcript=None):
        self.settings, self.project, self.id, self.profile, self.transcript = \
            settings, project, session_id, profile, transcript
        self.outputs = []

    def hook(self, name, **payload):
        out = run_hook(name, json.dumps({"session_id": self.id, "cwd": self.project, **payload}), self.settings,
                       profile=self.profile)
        self.outputs.append((name, out))
        return out

    def play(self, prompt=None, runs=(), start="startup", end=True):
        self.hook("session-start", source=start)
        if prompt:
            self.hook("user-prompt", prompt=prompt)
        self.hook("post-tool-use", tool_name="Edit", tool_input={"file_path": f"{self.project}/shop/orders.py"},
                  tool_response={})
        for passed, failed in runs:
            self.hook("post-tool-use", tool_name="Bash", tool_input={"command": "pytest -q"},
                      tool_response={"stdout": pytest_output(passed, failed)})
        self.hook("stop", stop_hook_active=False, **({"transcript_path": self.transcript} if self.transcript else {}))
        if end:
            return self.hook("session-end", reason="other")


def episodes(memrouter):
    return sorted(memrouter.store.recent(TEAM, 50), key=lambda e: e.provenance.created_at)


def test_a_lean_session_with_tests_becomes_one_episode(settings, task_store, memrouter, project_dir, log, transcript):
    """Ground truth from the hooks: the first prompt, the last test run's pass rate and counts, the turn's real
    tokens; no prediction, so surprise uses the similar-episodes fallback (low confidence)."""
    out = Session(settings, project_dir, "s1", transcript=transcript).play(
        "Add partial refunds to the orders module.", runs=[(3, 1), (4, 0)])
    assert out == ""  # the session-end hook still says nothing
    [ep] = episodes(memrouter)
    assert ep.situation == "Add partial refunds to the orders module." and ep.chosen.label == "session"
    assert ep.actual.success == 1.0 and ep.actual.signal_type == "auto"
    assert ep.actual.test_results.model_dump() == {"passed": 4, "failed": 0, "total": 4, "source": "hook",
                                                   "runner": None}
    assert ep.actual.tokens == 480
    assert ep.predicted.success is None and ep.low_confidence and ep.surprise == pytest.approx(0.5)  # 1.0 - 0.5
    assert ep.scope.project_id == project_dir and ep.provenance.agent_id == "claude-code"
    assert ep.scope.task_id == task_store.session_task(TEAM, project_dir, "s1") is not None
    assert {(c.key, c.value) for c in ep.conditions} == {("task_type", "session"), ("host", "claude-code")}
    assert [(r["session_id"], r["episode_id"], r["skipped"]) for r in log.episodes(TEAM)] == [("s1", ep.id, None)]


def test_each_segment_is_written_once_and_a_resumed_session_adds_its_own(settings, memrouter, project_dir, log):
    """A resumed session ends once per invocation: each segment is its own episode, about what was asked in
    it; nothing is written twice."""
    session = Session(settings, project_dir, "s1")
    session.play("Add partial refunds to the orders module.", runs=[(4, 0)])
    session.hook("session-end", reason="other")  # a second end with nothing new: no ground truth
    session.play("Now include refunds in the CSV export.", runs=[(4, 1)], start="resume")
    assert log.write(TEAM, lambda: pytest.fail("nothing is pending")) == {"written": [], "skipped": 0, "failed": 0}
    assert [e.situation for e in episodes(memrouter)] == ["Add partial refunds to the orders module.",
                                                          "Now include refunds in the CSV export."]
    assert [e.actual.success for e in episodes(memrouter)] == [1.0, 0.8]
    assert [r["skipped"] for r in log.episodes(TEAM)] == [None, "no ground truth", None]


def test_sessions_without_ground_truth_or_a_prompt_are_skipped_with_the_reason(settings, memrouter, project_dir,
                                                                              log):
    Session(settings, project_dir, "talk").play("Explain how refunds work.")  # no test run
    Session(settings, project_dir, "quiet").play(runs=[(2, 0)])  # no prompt
    assert episodes(memrouter) == []
    assert {r["session_id"]: r["skipped"] for r in log.episodes(TEAM)} == {"talk": "no ground truth",
                                                                         "quiet": "no prompt"}
    assert log.pending(TEAM) == []  # not reconsidered at every session end


def test_a_memory_failure_never_breaks_the_hook_and_the_episode_is_caught_up_later(settings, memrouter, project_dir,
                                                                                   log, monkeypatch):
    import horizon.eventlog.episodes as mod

    def down(*_):
        raise RuntimeError("embedder unavailable")

    monkeypatch.setattr(mod, "local_memory", down)
    out = Session(settings, project_dir, "s1").play("Add partial refunds to the orders module.", runs=[(4, 0)])
    assert out == "" and episodes(memrouter) == [] and len(log.pending(TEAM)) == 1
    monkeypatch.undo()
    Session(settings, project_dir, "s2").play("Add refunds to the CSV export.", runs=[(1, 1)])
    assert [e.situation for e in episodes(memrouter)] == ["Add partial refunds to the orders module.",
                                                          "Add refunds to the CSV export."]


def test_a_broken_episode_writer_never_changes_the_hook(settings, project_dir, monkeypatch):
    import horizon.hooks as hooks

    monkeypatch.setattr(hooks, "write_session_episodes", lambda *a: 1 / 0)
    assert Session(settings, project_dir, "s1").play("Add refunds.", runs=[(1, 0)]) == ""


@pytest.mark.parametrize("profile", ["observe", "full"])
def test_only_lean_writes_session_episodes(profile, settings, memrouter, project_dir, log):
    """observe records and nothing more; full has record_outcome's episodes, and session ones would double them."""
    Session(settings, project_dir, "s1", profile=profile).play("Add partial refunds.", runs=[(4, 0)])
    assert episodes(memrouter) == [] and log.episodes(TEAM) == []


def test_session_episodes_feed_memory_like_any_other(settings, memrouter, project_dir):
    """The next similar session's surprise is measured against their history, memory's forecast is scored,
    and recall finds them."""
    for i in (1, 2):
        Session(settings, project_dir, f"s{i}").play("Add partial refunds to the orders module.", runs=[(4, 0)])
    Session(settings, project_dir, "s3").play("Add partial refunds to the orders module.", runs=[(1, 1)])
    third = episodes(memrouter)[-1]
    assert third.low_confidence and third.surprise == pytest.approx(0.5 - 1.0)  # worse than its history
    [trust] = memrouter.predictor_trust(TEAM, "memory", "session")
    assert trust["samples"] == 2
    found = memrouter.recall(team_id=TEAM, situation="Add partial refunds to the orders module.")
    assert {m.episode_id for m in found.memories} >= {e.id for e in episodes(memrouter)[:2]}


def test_code_in_a_prompt_never_reaches_the_episode(settings, memrouter, project_dir):
    code = "```python\ndef leak_marker_fn(order):\n    return order.total * 0.9\n```"
    Session(settings, project_dir, "s1").play(f"Refunds are wrong, see:\n{code}\nFix it.", runs=[(1, 0)])
    [ep] = episodes(memrouter)
    assert "leak_marker_fn" not in json.dumps(ep.model_dump(mode="json")) and "Fix it." in ep.situation


def test_the_cli_backfills_a_log_recorded_without_episodes(settings, memrouter, project_dir, log, monkeypatch,
                                                           capsys):
    """An observe run (e.g. an eval's baseline arm) leaves only the log; `episodes-from-log` derives the
    episodes afterwards, which is the point of logging observations and deriving the rest."""
    Session(settings, project_dir, "s1", profile="observe").play("Add partial refunds.", runs=[(4, 0)])
    Session(settings, project_dir, "s2", profile="observe").play("Explain the refunds code.")
    monkeypatch.setenv("HORIZON_DB_URL", settings.db_url)
    monkeypatch.setenv("HORIZON_EMBEDDER", "hash")
    assert cli.main(["episodes-from-log", "--project", project_dir]) in (0, None)
    assert json.loads(capsys.readouterr().out) == {"written": 1, "skipped": 1, "failed": 0}
    assert [e.situation for e in episodes(memrouter)] == ["Add partial refunds."]
    cli.main(["episodes-from-log"])
    assert json.loads(capsys.readouterr().out) == {"written": 0, "skipped": 0, "failed": 0}


def age(log, session_id, hours):
    """Push a session's events into the past, as if it went quiet `hours` ago."""
    from horizon.models import now
    from horizon.taskstate.store import ts

    log.db.execute("UPDATE events SET created_at = %s WHERE session_id = %s",
                   (ts(log.db, now() - timedelta(hours=hours)), session_id))


def test_a_crashed_session_becomes_an_episode_once_it_has_been_idle(settings, memrouter, project_dir, log):
    """No SessionEnd (a crash, a killed terminal): the next session's start closes it after the idle timeout."""
    Session(settings, project_dir, "s1").play("Add partial refunds to the orders module.", runs=[(3, 1)], end=False)
    assert log.pending(TEAM) == []  # still open: it might carry on
    age(log, "s1", 3)
    Session(settings, project_dir, "s2").hook("session-start", source="startup")
    [ep] = episodes(memrouter)
    assert ep.situation == "Add partial refunds to the orders module." and ep.actual.success == 0.75
    assert [r["session_id"] for r in log.episodes(TEAM)] == ["s1"]  # the running session isn't closed


def test_a_session_quiet_for_less_than_the_timeout_stays_open(settings, memrouter, project_dir, log):
    Session(settings, project_dir, "s1").play("Add partial refunds.", runs=[(4, 0)], end=False)
    age(log, "s1", 1)  # the default timeout is 2 hours
    Session(settings, project_dir, "s2").hook("session-start", source="startup")
    assert episodes(memrouter) == [] and log.episodes(TEAM) == []
    short = dataclasses.replace(settings, session_idle_minutes=30)
    Session(short, project_dir, "s3").hook("session-start", source="startup")
    assert [r["session_id"] for r in log.episodes(TEAM)] == ["s1"]


def test_a_session_resumed_after_going_idle_splits_where_it_went_quiet(settings, memrouter, project_dir, log):
    """Its own resume closes the idle part first (before the resume is logged), and the rest is a new segment."""
    session = Session(settings, project_dir, "s1")
    session.play("Add partial refunds to the orders module.", runs=[(3, 1)], end=False)
    age(log, "s1", 5)
    session.play("Now include refunds in the CSV export.", runs=[(5, 0)], start="resume")
    assert [(e.situation, e.actual.success) for e in episodes(memrouter)] == [
        ("Add partial refunds to the orders module.", 0.75), ("Now include refunds in the CSV export.", 1.0)]
    assert log.pending(TEAM) == []


def test_an_idle_segment_with_no_ground_truth_is_skipped_once(settings, memrouter, project_dir, log):
    from horizon.models import now

    Session(settings, project_dir, "s1").play("Explain how refunds work.", end=False)
    later = now() + timedelta(hours=3)
    [seg] = log.pending(TEAM, at=later)
    assert (seg.after_n, seg.session_id) == (0, "s1")
    assert log.write(TEAM, lambda: pytest.fail("nothing to record"), at=later) == {"written": [], "skipped": 1,
                                                                                    "failed": 0}
    assert log.pending(TEAM, at=later) == [] and episodes(memrouter) == []


def test_draft_episode_is_pure_and_explains_what_it_skips(settings, project_dir, log):
    Session(settings, project_dir, "s1", profile="observe").play("Add partial refunds.", runs=[(3, 1)])
    [seg] = log.pending(TEAM)
    events = log.segment_events(TEAM, seg)
    snapshot = [e.model_dump() for e in events]
    first, second = draft_episode(events), draft_episode(events)
    assert first == second and first[1] is None and first[0].actual.success == 0.75
    assert [e.model_dump() for e in events] == snapshot
    assert draft_episode([]) == (None, "no events")
    other = [e.model_copy(update={"domain": "browser"}) for e in events]
    assert draft_episode(other) == (None, "no adapter for browser")


def test_memory_built_on_the_hooks_connection_matches_the_servers(settings):
    """The hook's memrouter uses the configured embedder and dimension, so its episodes are searchable by the
    MCP server's (same model name)."""
    from horizon.eventlog.episodes import local_memory
    from horizon.server import build_memrouter

    db = connect(settings.db_url)
    try:
        mine, server = local_memory(settings, db), build_memrouter(settings)
        assert isinstance(mine, MemRouter) and isinstance(mine.store, EpisodeStore)
        assert mine.embedder.model_name == server.embedder.model_name and mine.store.dim == server.store.dim
    finally:
        db.close()
