"""v2 Phase 1 track D: the context composer chooses what lean shows, and when, within a budget."""

import dataclasses
import json

import pytest
from test_session_briefing import REFUNDS, work
from test_session_episodes import Session

from horizon import composer
from horizon.composer import SESSION_START, Item, compose, label
from horizon.eventlog.briefing import HEADER
from horizon.hooks import LEAN_WORKFLOW

PREAMBLE = ("You are working through the invoicing service's backlog on your own: nobody will answer questions, so "
            "make reasonable choices and finish the ticket. ")
RULE = "Money must never be a float."


@pytest.fixture
def composed(settings):
    return dataclasses.replace(settings, context="composer")


def ticket(n: int) -> str:
    return PREAMBLE + f"Ticket #{n}: add feature {n} to the invoices." + (f" {RULE}" if n == 1 else "")


def backlog(settings, project, tickets: int) -> str:
    """One session per ticket; returns what the next session starts with."""
    for n in range(1, tickets + 1):
        Session(settings, project, f"t{n}").play(ticket(n), runs=[(3, 0)])
    out = Session(settings, project, "next").hook("session-start", source="startup")
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


# --- what is composed -----------------------------------------------------------------------------------

def test_classic_is_the_default(settings):
    """Principle 5: the composer is on only in the evals' candidate arm until it passes the gate."""
    assert settings.context == "classic"


def test_a_rule_from_the_first_task_still_reaches_the_tenth(settings, composed, project_dir, tmp_path):
    """Both lean's classic context and the composer carry every constraint the project's tasks set, not only
    the latest three tasks' (handoff: a rule given in ticket 1 for ticket 6 was gone by then)."""
    classic = backlog(settings, project_dir, 10)
    assert f"them):\n- {RULE}" in classic
    now = backlog(composed, str(tmp_path / "other"), 10)
    assert now.startswith(LEAN_WORKFLOW)
    assert f"{SESSION_START[3].heading}\n- {RULE}" in now


def test_earlier_tasks_are_labelled_by_what_differs(settings, composed, project_dir, tmp_path):
    """Every ticket opens with the same preamble; cut at 200 characters, the lines would be nearly all
    preamble. Both lean's classic context and the composer's say which ticket each was."""
    classic = backlog(settings, project_dir, 4)
    now = backlog(composed, str(tmp_path / "other"), 4)
    recent = now.split(SESSION_START[4].heading + "\n", 1)[1].splitlines()
    assert recent == ["- …Ticket #4: add feature 4 to the invoices.", "- …Ticket #3: add feature 3 to the invoices.",
                      "- …Ticket #2: add feature 2 to the invoices."]
    assert "- …Ticket #3: add feature 3 to the invoices." in classic.splitlines()
    assert PREAMBLE.strip() not in classic and PREAMBLE.strip() not in now
    assert "start_task" not in classic and "recall_context" not in classic


def test_labels_cut_only_what_every_goal_shares():
    goals = [ticket(12), ticket(13), ticket(2)]
    assert [label(g, goals) for g in goals] == [f"…Ticket #{n}: add feature {n} to the invoices." for n in (12, 13, 2)]
    assert label("Fix the login bug.", ["Fix the login bug.", "Add refunds."]) == "Fix the login bug."
    assert label(ticket(1), [ticket(1), "Add refunds."]) == ticket(1)[:119] + "…"  # one outlier: nothing is shared
    long = [PREAMBLE + "x" * 300, PREAMBLE + "y"]
    assert label(long[0], long, most=50) == "…" + "x" * 48 + "…"


def test_each_line_once_in_the_most_essential_section():
    items = [Item("constraints", "- Ids are uuid4 hex."), Item("team_rules", "- Ids are  uuid4 hex"),
             Item("constraints", "- Money is cents.")]
    text, dropped = compose("P", SESSION_START, items, 10_000)
    assert text.splitlines() == ["P", SESSION_START[2].heading, "- Ids are  uuid4 hex",
                                 SESSION_START[3].heading, "- Money is cents."]
    assert dropped == {}


def test_a_tight_budget_drops_the_least_essential_kinds_first():
    """Recent tasks go first, then pitfalls; team rules and the active task last. Whole lines, no empty
    headings, never over budget."""
    items = [Item("active", "- the task"), Item("team_rules", "- " + "r" * 60), Item("constraints", "- " + "c" * 60),
             Item("pitfalls", "- " + "p" * 60), Item("earlier", "- " + "e" * 60)]
    full, _ = compose("P", SESSION_START, items, 10_000)
    for budget, gone in ((len(full) - 1, {"earlier": 1}), (len(full) - 130, {"earlier": 1, "pitfalls": 1})):
        text, dropped = compose("P", SESSION_START, items, budget)
        assert len(text) <= budget and dropped == gone
        assert all(s.heading not in text for s in SESSION_START if s.key in gone)
    text, dropped = compose("P", SESSION_START, items, 1)
    assert text == "P" and len(dropped) == 5


def test_the_budget_setting_bounds_the_block(composed, project_dir):
    small = dataclasses.replace(composed, context_budget_chars=len(LEAN_WORKFLOW) + 150)
    ctx = backlog(small, project_dir, 6)
    assert len(ctx) <= small.context_budget_chars and RULE in ctx  # the constraint outranks recent tasks


def test_a_resumed_session_sees_its_task_first(composed, project_dir):
    s = Session(composed, project_dir, "s1")
    s.play(ticket(1), end=False)
    ctx = json.loads(s.hook("session-start", source="resume"))["hookSpecificOutput"]["additionalContext"]
    assert ctx.splitlines()[1:3] == [SESSION_START[0].heading, f"- {ticket(1)}"]


# --- re-anchoring after a compaction (Phase 2) ------------------------------------------------------------

LONG_GOAL = ("Build `tab`, a package for sharing expenses. These constraints hold for the whole job: money is always "
             "an integer number of cents, never a float; standard library only. " + "Part detail. " * 60)


def compacted(settings, project, source="compact"):
    """A session that set its task, said more, ran tests and edited, then was compacted; returns the new start."""
    s = Session(settings, project, "s1")
    s.play(LONG_GOAL, runs=[(3, 1), (3, 1)], end=False)
    s.hook("user-prompt", prompt="Also keep the CSV export stable.")
    s.hook("user-prompt", prompt="/compact")
    out = s.hook("session-start", source=source)
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_after_a_compaction_the_task_comes_back_whole_with_its_progress(composed, project_dir):
    from horizon.composer import REANCHOR

    ctx = compacted(composed, project_dir)
    lines = ctx.splitlines()
    assert lines[1:3] == [REANCHOR[0].heading, f"- {LONG_GOAL.strip()}"]  # whole: past classic's 200 characters
    progress = ctx.split(REANCHOR[1].heading + "\n", 1)[1].splitlines()[:3]
    assert progress == ["- a later message: Also keep the CSV export stable.",
                        "- last test run: 3/4 passing, after 2 runs (2 failing); still failing: tests/test_o.py::test_refund",
                        "- files changed: shop/orders.py"]
    assert "/compact" not in ctx and len(ctx) <= composed.context_budget_chars


def test_only_a_compaction_restates_the_task(settings, composed, project_dir, tmp_path):
    """A resumed session still has its conversation; classic lean re-shows only the start of the goal."""
    from horizon.composer import REANCHOR

    assert REANCHOR[1].heading not in compacted(composed, project_dir, source="resume")
    classic = compacted(settings, str(tmp_path / "other"))
    assert REANCHOR[1].heading not in classic and LONG_GOAL[:1000] not in classic


def test_restating_stays_within_the_budget_however_long_the_session(composed, project_dir):
    s = Session(composed, project_dir, "s1")
    s.play(LONG_GOAL * 3, runs=[(1, 3)] * 20, end=False)
    for i in range(30):
        s.hook("user-prompt", prompt=f"Follow-up number {i}: " + "more detail " * 30)
    ctx = json.loads(s.hook("session-start", source="compact"))["hookSpecificOutput"]["additionalContext"]
    assert len(ctx) <= composed.context_budget_chars and "Follow-up number 29" in ctx and "number 24" not in ctx


# --- when: the past-sessions note needs the forecast to expect trouble -----------------------------------

@pytest.fixture
def on(composed):
    return dataclasses.replace(composed, session_memory="on", session_memory_min_similarity=0.5)


def test_one_troubled_session_among_clean_ones_is_a_fluke(on, project_dir):
    """Three clean sessions and one with a failing run: the forecast expects well under one failing run, so the
    note (which would show the troubled one) stays out. Without the composer it shows."""
    for i in range(3):
        work(on, project_dir, f"ok{i}", REFUNDS)
    work(on, project_dir, "bad", REFUNDS, runs=[(3, 1), (4, 0)])
    assert HEADER not in work(on, project_dir, "now", REFUNDS)
    assert HEADER in work(dataclasses.replace(on, context="classic"), project_dir, "now2", REFUNDS)


def test_trouble_that_keeps_happening_is_shown(on, project_dir):
    for i in range(2):
        work(on, project_dir, f"bad{i}", REFUNDS, runs=[(3, 1), (4, 0)])
    said = work(on, project_dir, "now", REFUNDS)
    assert HEADER in said and "(2 sessions went this way)" in said


def test_one_embedder_serves_the_forecast_and_the_note(on, project_dir, monkeypatch):
    from horizon.eventlog import episodes

    for i in range(2):
        work(on, project_dir, f"bad{i}", REFUNDS, runs=[(3, 1), (4, 0)])
    built, real = [], episodes.local_memory
    monkeypatch.setattr(episodes, "local_memory", lambda s, db: built.append(1) or real(s, db))
    s = Session(on, project_dir, "now")
    s.hook("session-start", source="startup")
    built.clear()
    assert HEADER in s.hook("user-prompt", prompt=REFUNDS) and built == [1]


def test_a_failing_forecast_never_stops_the_task_starting(on, project_dir, monkeypatch):
    work(on, project_dir, "bad", REFUNDS, runs=[(3, 1), (4, 0)])
    monkeypatch.setattr(composer, "expects_trouble", lambda *a, **k: 1 / 0)
    assert work(on, project_dir, "now", REFUNDS).startswith("Horizon task id:")


# --- hosted lean ---------------------------------------------------------------------------------------

def test_hosted_lean_uses_the_composer_when_the_server_is_set_to(composed):
    from fastapi.testclient import TestClient

    from horizon.accounts import Accounts
    from horizon.db import connect
    from horizon.server import http_app

    for s, composes in ((composed, True), (dataclasses.replace(composed, context="classic"), False)):
        client = TestClient(http_app(s))
        _, key = Accounts(connect(s.db_url)).create_team(f"Acme-{composes}")
        headers = {"Authorization": f"Bearer {key}"}
        for n in (1, 2):
            client.post("/api/hooks/lean-session-start", json={"project": "p", "session_id": f"s{n}"}, headers=headers)
            client.post("/api/hooks/user-prompt", json={"project": "p", "session_id": f"s{n}", "prompt": ticket(n)},
                        headers=headers)
        ctx = client.post("/api/hooks/lean-session-start", json={"project": "p", "session_id": "s3"},
                          headers=headers).json()["hookSpecificOutput"]["additionalContext"]
        assert (SESSION_START[4].heading in ctx) is composes and RULE in ctx
        client.post("/api/hooks/user-prompt", json={"project": "p", "session_id": "s3", "prompt": ticket(3)},
                    headers=headers)
        ctx = client.post("/api/hooks/lean-session-start", json={"project": "p", "session_id": "s3",
                                                                 "source": "compact"},
                          headers=headers).json()["hookSpecificOutput"]["additionalContext"]
        assert ("restated: the context was just compacted" in ctx) is composes
