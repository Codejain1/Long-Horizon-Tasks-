"""v2 Phase 1 track C: world model v2 forecasts how a session will go, with Jev as the prior."""

import dataclasses
import json

import pytest
from test_session_episodes import TEAM, TRANSCRIPT, Session

from horizon import cli
from horizon.decision.scorers import Answer
from horizon.eventlog.forecast import FAILED_RUN_LEVELS, SessionWorldModel, replay
from horizon.memrouter.embedding import HashEmbedder

EASY = "Add partial refunds to the orders module."  # passes first time
HARD = "Migrate the billing database to the new schema."  # 4 failing runs, ends at 3/4
EASY_RUNS, HARD_RUNS = [(4, 0)], [(1, 3), (2, 2), (2, 2), (3, 1)]


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in TRANSCRIPT))
    return str(path)


def model(log, settings, scorer=None):
    return SessionWorldModel(log, HashEmbedder(settings.embedding_dim), settings, scorer)


def history(settings, project, transcript, easy=3, hard=3, prefix="s"):
    for i in range(max(easy, hard)):
        if i < easy:
            Session(settings, project, f"{prefix}e{i}", transcript=transcript).play(EASY, runs=EASY_RUNS)
        if i < hard:
            Session(settings, project, f"{prefix}h{i}", transcript=transcript).play(HARD, runs=HARD_RUNS)


class Prior:
    name = "jev"

    def __init__(self, success=0.2, failed_runs=0.5, fail=False):
        self.success, self.failed_runs, self.fail, self.calls = success, failed_runs, fail, []

    def ask(self, state, qs):
        if self.fail:
            raise RuntimeError("jev unavailable")
        self.calls.append((state, qs))
        return {"success": Answer(self.success), "failed_runs": Answer(self.failed_runs, 0.9)}


def test_no_history_is_the_prior_with_no_confidence(settings, project_dir, log):
    f = model(log, settings).predict(TEAM, project_dir, EASY)
    assert (f.success, f.confidence, f.samples, f.prior, f.failed_runs, f.tokens) == (0.5, 0.0, 0.0, "base_rate",
                                                                                     None, None)


def test_similar_past_sessions_drive_the_forecast(settings, project_dir, log, transcript):
    """Three of each kind: a task like the easy ones is forecast easy, one like the hard ones hard, each shrunk a
    little towards the team's base rate (1 pseudo-session: success 0.875, 2 failing runs). The two kinds share a
    few words, so each also carries a trace (about 0.03) of the other."""
    history(settings, project_dir, transcript)
    m = model(log, settings)
    easy, hard = m.predict(TEAM, project_dir, EASY), m.predict(TEAM, project_dir, HARD)
    near = lambda x: pytest.approx(x, abs=0.03)  # noqa: E731
    assert easy.samples == near(3.0) and easy.confidence == near(0.5)
    assert easy.success == near((3 * 1.0 + 0.875) / 4) and easy.failed_runs == near(2.0 / 4)
    assert hard.success == near((3 * 0.75 + 0.875) / 4) and hard.failed_runs == near((3 * 4 + 2.0) / 4)
    assert (easy.failed_runs_p90, hard.failed_runs_p90) == (0.0, 4.0)
    assert (easy.tokens, easy.tokens_p90, easy.turns) == (480.0, 480.0, 1.0)


def test_an_unrelated_task_leans_on_the_prior(settings, project_dir, log, transcript):
    history(settings, project_dir, transcript)
    f = model(log, settings).predict(TEAM, project_dir, "Write the onboarding guide for new contributors.")
    assert f.samples < 0.1 and f.success == pytest.approx(0.875, abs=0.03) and f.failed_runs_p90 is None


def test_other_projects_count_less_and_walls_shut_them_out(settings, project_dir, log, transcript, tmp_path):
    history(settings, project_dir, transcript, easy=3, hard=0)
    other = str(tmp_path / "other")
    assert model(log, settings).predict(TEAM, other, EASY).samples == pytest.approx(3 * 0.85)
    walled = dataclasses.replace(settings, other_project_factor=0.0)
    assert model(log, walled).predict(TEAM, other, EASY).samples == 0.0


def test_jev_is_the_prior_until_similar_sessions_take_over(settings, project_dir, log, transcript):
    """With nothing to go on, the forecast is Jev's reading of the task (worth 2 pseudo-sessions). Once the project
    has sessions, Jev is blended with their base rate (3 sessions against Jev's 2) and similar sessions outweigh the
    prior. Jev reads the task only."""
    jev = Prior(success=0.2, failed_runs=0.5)  # level 2 of 5: "2 or 3" failing runs, 2.5
    alone = model(log, settings, jev).predict(TEAM, project_dir, EASY)
    assert (alone.success, alone.failed_runs, alone.prior, alone.confidence) == (0.2, 2.5, "jev", 0.0)
    assert jev.calls[0][0] == {"task": EASY} and jev.calls[0][1]["failed_runs"]["criteria"] == FAILED_RUN_LEVELS
    history(settings, project_dir, transcript, easy=3, hard=0)
    f = model(log, settings, jev).predict(TEAM, project_dir, EASY)
    prior_success, prior_runs = (3 * 1.0 + 2 * 0.2) / 5, (3 * 0.0 + 2 * 2.5) / 5
    assert f.success == pytest.approx((3 * 1.0 + 2 * prior_success) / 5)
    assert f.failed_runs == pytest.approx((3 * 0.0 + 2 * prior_runs) / 5)


def test_a_failing_scorer_falls_back_to_the_base_rate(settings, project_dir, log, transcript):
    history(settings, project_dir, transcript, easy=1, hard=1)
    f = model(log, settings, Prior(fail=True)).predict(TEAM, project_dir, EASY)
    assert f.prior == "base_rate"


def test_a_running_session_is_compared_with_sessions_that_got_as_far(settings, project_dir, log, transcript):
    """Stall detection's question (Phase 2): after 2 failing runs, only sessions that also hit 2 or more count."""
    for i in range(2):
        Session(settings, project_dir, f"ok{i}", transcript=transcript).play(HARD, runs=[(4, 0)])
        Session(settings, project_dir, f"bad{i}", transcript=transcript).play(HARD, runs=HARD_RUNS)
    m = model(log, settings, Prior())
    fresh = m.predict(TEAM, project_dir, HARD)
    stuck = m.predict(TEAM, project_dir, HARD, so_far={"failed_runs": 2})
    assert fresh.prior == "jev" and stuck.prior == "base_rate"  # Jev read the task, not how it's going
    assert stuck.samples == pytest.approx(2.0) and stuck.success == pytest.approx(0.75)
    assert stuck.failed_runs == pytest.approx(4.0) and fresh.samples == pytest.approx(4.0)


def test_replay_forecasts_each_session_from_earlier_ones_only(settings, project_dir, log, transcript):
    history(settings, project_dir, transcript, easy=4, hard=4)
    r = replay(model(log, settings), TEAM)
    assert (r["sessions"], r["forecast"]) == (8, 7)
    assert r["brier"]["model"] < r["brier"]["base_rate"] and r["versus"]["base_rate"]["advantage"] > 0
    assert r["failed_runs_mae"]["model"] < r["failed_runs_mae"]["base_rate"]
    assert set(r["versus"]["fallback"]) == {"pairs", "advantage", "advantage_se", "clearly_better"}
    assert r["tokens_mean_abs_log_error"] == 0.0
    jev = replay(model(log, settings, Prior(success=0.9)), TEAM)
    assert "jev_prior" in jev["brier"] and len(jev["brier"]) == 4


def test_replay_needs_two_sessions(settings, log):
    assert replay(model(log, settings), TEAM) == {"sessions": 0, "forecast": 0}


def test_the_cli_reports_the_replay(settings, project_dir, transcript, monkeypatch, capsys):
    history(settings, project_dir, transcript, easy=2, hard=2)
    monkeypatch.setenv("HORIZON_DB_URL", settings.db_url)
    monkeypatch.setenv("HORIZON_EMBEDDER", "hash")
    assert cli.main(["eval-forecast"]) in (0, None)
    out = json.loads(capsys.readouterr().out)
    assert out["model"] == "session-v2" and out["forecast"] == 3
    assert cli.main(["eval-forecast", "--jev"]) == 1  # no scorer configured
