"""v2 Phase 1 track B: Jev judges the outcome of session segments that have no test run."""

import dataclasses
import json

import pytest
from test_session_episodes import TEAM, Session, episodes

from horizon import cli
from horizon.decision import scorers
from horizon.decision.scorers import Answer
from horizon.eventlog.judge import LEVELS, evidence, questions

REFUNDS = "Add partial refunds to the orders module."


class Judge:
    """Scripted Jev: the outcome level's position and confidence, a correction probability per follow-up."""

    name = "jev"

    def __init__(self, outcome=0.75, confidence=0.9, corrections=(), fail=False):
        self.outcome, self.confidence, self.corrections, self.fail = outcome, confidence, corrections, fail
        self.calls = []

    def ask(self, state, qs):
        if self.fail:
            raise RuntimeError("jev unavailable")
        if "outcome" in qs:
            self.calls.append((state, qs))
        out = {}
        for qid in qs:
            if qid == "outcome":
                out[qid] = Answer(self.outcome, self.confidence)
            elif qid.startswith("correction_"):
                i = int(qid.split("_")[1])
                out[qid] = Answer(self.corrections[i] if i < len(self.corrections) else 0.0)
            else:  # the rule classifier's and the attention filter's questions
                out[qid] = Answer(1.0 if qid.startswith("m") else 0.0)
        return out


@pytest.fixture
def judge(settings, monkeypatch):
    """The owner configured Jev (HORIZON_SCORER=jev); the scorer is scripted."""
    j, built = Judge(), []

    def make(kind, *a):
        built.append(kind)
        return j

    monkeypatch.setattr(scorers, "make_scorer", make)
    monkeypatch.setattr("horizon.hooks.make_scorer", make)
    j.built = built
    return j, dataclasses.replace(settings, scorer="jev")


def talk(settings, project, sid, prompts, edits=1, end=True):
    s = Session(settings, project, sid)
    s.hook("session-start", source="startup")
    for i, p in enumerate(prompts):
        s.hook("user-prompt", prompt=p)
        for _ in range(edits if i == 0 else 0):
            s.hook("post-tool-use", tool_name="Edit", tool_input={"file_path": f"{project}/shop/orders.py"},
                   tool_response={})
        s.hook("stop", stop_hook_active=False)
    if end:
        s.hook("session-end", reason="other")


def test_a_session_without_tests_gets_a_judged_outcome(judge, memrouter, project_dir, log):
    j, settings = judge
    j.outcome, j.corrections = 0.5, (0.9, 0.1)
    talk(settings, project_dir, "s1", [REFUNDS, "No, refunds over the total must be rejected.", "Thanks, works."])
    [ep] = episodes(memrouter)
    assert ep.situation == REFUNDS and ep.actual.success == 0.5
    assert ep.actual.signal_type == "implicit" and ep.actual.test_results is None  # weighs less than a test run
    [(state, qs)] = j.calls
    assert state == {"task": REFUNDS, "follow_ups": ["No, refunds over the total must be rejected.", "Thanks, works."],
                     "activity": {"edits": 1, "files_changed": 1, "commands": 0, "turns": 3}, "ended": "other"}
    assert set(qs) == {"outcome", "correction_0", "correction_1"} and qs["outcome"]["criteria"] == LEVELS
    assert project_dir not in json.dumps(state)  # counts, never paths


def test_tests_are_ground_truth_and_the_judge_is_not_asked(judge, memrouter, project_dir):
    j, settings = judge
    Session(settings, project_dir, "s1").play(REFUNDS, runs=[(4, 0)])
    assert [e.actual.signal_type for e in episodes(memrouter)] == ["auto"] and j.calls == []


def test_nothing_to_judge_means_no_call_and_no_episode(judge, memrouter, project_dir, log):
    """A single question with no edit: no evidence of an outcome, so Jev isn't paid to guess."""
    j, settings = judge
    talk(settings, project_dir, "s1", ["Explain how refunds work."], edits=0)
    assert j.calls == [] and episodes(memrouter) == []
    assert [r["skipped"] for r in log.episodes(TEAM)] == ["no ground truth"]


def test_an_unsure_verdict_is_not_memory(judge, memrouter, project_dir, log):
    j, settings = judge
    j.confidence = 0.3
    talk(settings, project_dir, "s1", [REFUNDS])
    assert episodes(memrouter) == [] and [r["skipped"] for r in log.episodes(TEAM)] == ["judge unsure"]


def test_done_contradicted_by_a_last_correction_is_unsure(judge, memrouter, project_dir, log):
    j, settings = judge
    j.outcome, j.corrections = 0.9, (0.1, 0.8)
    talk(settings, project_dir, "s1", [REFUNDS, "Thanks.", "Wait, the total is wrong now."])
    assert episodes(memrouter) == [] and [r["skipped"] for r in log.episodes(TEAM)] == ["judge unsure"]


def test_a_failing_judge_never_breaks_the_hook(judge, memrouter, project_dir, log):
    j, settings = judge
    j.fail = True
    s = Session(settings, project_dir, "s1")
    s.hook("session-start", source="startup")
    s.hook("user-prompt", prompt=REFUNDS)
    s.hook("post-tool-use", tool_name="Edit", tool_input={"file_path": f"{project_dir}/a.py"}, tool_response={})
    assert s.hook("session-end", reason="other") == ""
    assert episodes(memrouter) == [] and [r["skipped"] for r in log.episodes(TEAM)] == ["no ground truth (judge failed)"]


def test_without_a_configured_scorer_nothing_is_judged(settings, memrouter, project_dir, log, monkeypatch):
    monkeypatch.setattr(scorers, "make_scorer", lambda *a: pytest.fail("no scorer is configured"))
    talk(settings, project_dir, "s1", [REFUNDS, "Now reject refunds over the total."])
    assert episodes(memrouter) == [] and [r["skipped"] for r in log.episodes(TEAM)] == ["no ground truth"]


def test_one_scorer_serves_the_whole_batch(judge, memrouter, project_dir, log):
    """Two segments recorded by observe, written in one go: the scorer is built once."""
    j, settings = judge
    for sid in ("s1", "s2"):
        s = Session(settings, project_dir, sid, profile="observe")
        s.hook("session-start", source="startup")
        s.hook("user-prompt", prompt=REFUNDS)
        s.hook("post-tool-use", tool_name="Edit", tool_input={"file_path": f"{project_dir}/a.py"}, tool_response={})
        s.hook("session-end", reason="other")
    from horizon.hooks import write_session_episodes

    write_session_episodes(log.db, settings, project_dir)
    assert len(episodes(memrouter)) == 2 and len(j.calls) == 2 and j.built == ["jev"]


def test_rejudge_reconsiders_what_had_no_ground_truth(judge, settings, memrouter, project_dir, log, monkeypatch,
                                                      capsys):
    """A log recorded before Jev was configured: `episodes-from-log --rejudge` gives those segments outcomes."""
    talk(settings, project_dir, "s1", [REFUNDS, "Also reject refunds over the total."])  # no scorer yet
    assert [r["skipped"] for r in log.episodes(TEAM)] == ["no ground truth"]
    monkeypatch.setenv("HORIZON_DB_URL", settings.db_url)
    monkeypatch.setenv("HORIZON_EMBEDDER", "hash")
    monkeypatch.setenv("HORIZON_SCORER", "jev")
    cli.main(["episodes-from-log"])
    assert json.loads(capsys.readouterr().out) == {"written": 0, "skipped": 0, "failed": 0}  # not without --rejudge
    cli.main(["episodes-from-log", "--rejudge"])
    assert json.loads(capsys.readouterr().out) == {"written": 1, "skipped": 0, "failed": 0}
    assert [e.actual.signal_type for e in episodes(memrouter)] == ["implicit"]


def test_the_judge_reads_redacted_prompts_and_counts_only(settings, project_dir, log):
    code = "```python\ndef leak_marker_fn(order):\n    return order.total * 0.9\n```"
    s = Session(settings, project_dir, "s1", profile="observe")
    s.hook("session-start", source="startup")
    s.hook("user-prompt", prompt=REFUNDS)
    s.hook("user-prompt", prompt=f"Still wrong:\n{code}")
    s.hook("post-tool-use", tool_name="Bash", tool_input={"command": "python -c 'print(1)'"}, tool_response={})
    s.hook("session-end", reason="other")
    [seg] = log.pending(TEAM)
    state = evidence(log.segment_events(TEAM, seg))
    assert "leak_marker_fn" not in json.dumps(state) and state["activity"]["commands"] == 1
    assert evidence([]) is None
    assert set(questions(2)) == {"outcome", "correction_0", "correction_1"}
