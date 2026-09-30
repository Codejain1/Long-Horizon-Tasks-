"""v2 Phase 2: a session episode's surprise is measured against the session forecast, once it earns it."""

import dataclasses

import pytest
from test_session_episodes import Session

from horizon.db import connect, load_json
from horizon.eventlog import forecast
from horizon.eventlog.forecast import gate

TASK = "Add partial refunds to the orders module."


def episodes(settings) -> list[dict]:
    db = connect(settings.db_url)
    rows = db.fetchall("SELECT e.data FROM session_episodes s JOIN episodes e ON e.id = s.episode_id"
                       " WHERE s.episode_id IS NOT NULL ORDER BY s.through_n")
    return [load_json(r[0]) for r in rows]


def history(settings, project, runs_each=((3, 1), (4, 0)), n=2):
    for i in range(n):
        Session(settings, project, f"s{i}").play(TASK, runs=list(runs_each))


def test_auto_keeps_memorys_fallback_until_the_gate_passes(settings, project_dir, monkeypatch):
    replays = []
    monkeypatch.setattr(forecast, "replay", lambda *a, **k: replays.append(1) or {})
    assert settings.session_surprise == "auto"
    history(settings, project_dir)
    eps = episodes(settings)
    assert [e["low_confidence"] for e in eps] == [True, True] and all(e["predicted"]["success"] is None for e in eps)
    assert replays == []  # 1 pair < 30: not even replayed


def test_forecast_mode_records_the_forecast_as_the_prediction(settings, project_dir):
    s = dataclasses.replace(settings, session_surprise="forecast")
    history(s, project_dir, n=3)
    first, second, third = episodes(s)
    assert first["low_confidence"] is True and first["predicted"]["success"] is None  # nothing to go on yet
    assert second["low_confidence"] is False and second["predicted"]["source"] == "world_model"
    assert second["predicted"]["success"] == pytest.approx((1.0 + 1.0) / 2)  # one session (success 1) + its prior
    assert second["predicted"]["tokens"] is None or second["predicted"]["tokens"] > 0
    assert third["surprise"] == pytest.approx(0.0, abs=1e-6)  # expected, so nothing to learn


def test_forecast_accuracy_is_tracked_as_its_own_predictor(settings, project_dir):
    from horizon.eventlog.episodes import local_memory

    s = dataclasses.replace(settings, session_surprise="forecast")
    history(s, project_dir, n=3)
    sources = {t["source"] for t in local_memory(s, connect(s.db_url)).predictor_trust(s.team_id)}
    assert "world_model" in sources and "memory" not in sources


def test_the_gate_needs_enough_pairs_and_a_clear_win(settings, project_dir, monkeypatch):
    from horizon.eventlog.episodes import SessionEpisodes
    from horizon.memrouter.embedding import make_embedder

    history(settings, project_dir, n=4)
    model = forecast.SessionWorldModel(SessionEpisodes(connect(settings.db_url)), make_embedder("hash", 384), settings)
    win = lambda pairs, clear: {"versus": {"fallback": {"pairs": pairs, "clearly_better": clear}}}  # noqa: E731
    monkeypatch.setattr(forecast, "replay", lambda *a, **k: win(3, True))
    assert gate(model, settings.team_id, 3) is True
    assert gate(model, settings.team_id, 4) is False  # 4 sessions make only 3 pairs
    monkeypatch.setattr(forecast, "replay", lambda *a, **k: win(3, False))
    assert gate(model, settings.team_id, 3) is False


def test_auto_switches_to_the_forecast_once_the_gate_passes(settings, project_dir, monkeypatch):
    s = dataclasses.replace(settings, world_model_min_pairs=1)
    monkeypatch.setattr(forecast, "gate", lambda *a: True)
    history(s, project_dir, n=3)
    assert [e["predicted"]["source"] for e in episodes(s)][1:] == ["world_model"] * 2


def test_a_failing_forecast_never_stops_the_episode(settings, project_dir, monkeypatch):
    s = dataclasses.replace(settings, session_surprise="forecast")
    monkeypatch.setattr(forecast.SessionWorldModel, "predict", lambda *a, **k: 1 / 0)
    history(s, project_dir)
    assert [e["low_confidence"] for e in episodes(s)] == [True, True]


def test_fallback_mode_never_builds_a_forecast(settings, project_dir, monkeypatch):
    s = dataclasses.replace(settings, session_surprise="fallback")
    monkeypatch.setattr(forecast.SessionWorldModel, "__init__", lambda *a, **k: 1 / 0)
    history(s, project_dir)
    assert len(episodes(s)) == 2
