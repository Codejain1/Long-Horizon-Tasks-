import dataclasses

import numpy as np
import pytest

from horizon.memrouter.embedding import HashEmbedder, make_embedder
from horizon.memrouter.router import MemRouter
from horizon.models import Actual, Condition, Predicted, TestResults


def test_hash_embedder_is_deterministic_and_normalised():
    e = HashEmbedder(384)
    a, b = e.embed(["use sqlalchemy for the orm"]), e.embed(["use sqlalchemy for the orm"])
    assert np.array_equal(a, b)
    assert a.shape == (1, 384)
    assert np.linalg.norm(a[0]) == pytest.approx(1.0, abs=1e-5)


def test_make_embedder_falls_back_to_hash_offline():
    # Hugging Face is unreachable in CI/sandbox; fastembed must degrade, not crash.
    assert make_embedder("hash").model_name.startswith("hash")
    assert make_embedder("fastembed").dim == 384
    with pytest.raises(ValueError):
        make_embedder("nope")


def test_record_with_prediction(memrouter, episode_store):
    r = memrouter.record(team_id="local", situation="choose an http client", chosen="httpx",
                         predicted=Predicted(success=0.9), actual=Actual(success=1.0),
                         conditions=[Condition(key="python", op=">=", value="3.12")], task_id="t1")
    assert not r.episode.low_confidence
    assert r.predicted_success_used == 0.9
    assert r.episode.surprise == pytest.approx(0.1)
    stored = episode_store.get(r.episode.id, "local")
    assert stored.conditions[0].key == "python"
    assert stored.embedding_model == memrouter.embedder.model_name
    assert stored.provenance.task_id == "t1"


def test_no_prediction_and_no_history_uses_half_and_flags_low_confidence(memrouter):
    r = memrouter.record(team_id="local", situation="choose an http client", chosen="httpx",
                         actual=Actual(success=1.0))
    assert r.episode.low_confidence
    assert r.predicted_success_used == 0.5
    assert r.episode.surprise == pytest.approx(0.5)


def test_no_prediction_uses_similar_history(memrouter):
    for success in (1.0, 0.0, 0.5):
        memrouter.record(team_id="local", situation="migrate the users table to add an email column",
                         chosen="alembic migration", predicted=Predicted(success=0.5), actual=Actual(success=success))
    # Unrelated episode must not count as similar.
    memrouter.record(team_id="local", situation="tune css grid layout for mobile", chosen="flexbox",
                     predicted=Predicted(success=0.5), actual=Actual(success=0.0))

    r = memrouter.record(team_id="local", situation="migrate the users table to add an email column",
                         chosen="alembic migration", actual=Actual(success=1.0))
    assert r.episode.low_confidence
    assert r.predicted_success_used == pytest.approx(0.5)  # mean of 1.0, 0.0, 0.5
    # Other teams' history is not used.
    r2 = memrouter.record(team_id="team-b", situation="migrate the users table to add an email column",
                          chosen="alembic migration", actual=Actual(success=1.0))
    assert r2.predicted_success_used == 0.5


def test_recall_returns_similar_outcomes_first(memrouter, episode_store):
    memrouter.record(team_id="local", situation="choose a date parsing library for python",
                     chosen="dateutil", predicted=Predicted(success=0.8),
                     actual=Actual(success=0.25, test_results=TestResults(passed=1, failed=3, total=4)))
    memrouter.record(team_id="local", situation="set up github actions caching for node",
                     chosen="actions/cache", predicted=Predicted(success=0.8), actual=Actual(success=1.0))

    s = memrouter.recall(team_id="local", situation="which python library to parse dates", task_id="t9")
    assert s.memories[0].chosen == "dateutil"
    assert s.memories[0].outcome == "tests 1/4 passed, worse than predicted"
    assert episode_store.get_recall(s.recall_id) == [m.episode_id for m in s.memories]


def test_recall_is_team_isolated(memrouter):
    memrouter.record(team_id="team-a", situation="choose a cache", chosen="redis", actual=Actual(success=1.0))
    assert memrouter.recall(team_id="team-b", situation="choose a cache").memories == []


def test_recall_respects_token_budget_and_item_cap(episode_store, settings):
    router = MemRouter(episode_store, HashEmbedder(384), dataclasses.replace(settings, recall_max_items=3))
    for i in range(6):
        router.record(team_id="local", situation=f"choose a logging setup variant {i}", chosen="structlog",
                      actual=Actual(success=1.0))
    capped = router.recall(team_id="local", situation="choose a logging setup")
    assert len(capped.memories) == 3 and capped.truncated

    tiny = router.recall(team_id="local", situation="choose a logging setup", token_budget=100)
    assert 0 < len(tiny.memories) < 3
    zero = router.recall(team_id="local", situation="choose a logging setup", token_budget=1)
    assert zero.memories == [] and zero.truncated


def test_dimension_mismatch_is_rejected(episode_store, settings):
    with pytest.raises(ValueError):
        MemRouter(episode_store, HashEmbedder(128), settings)


def test_condition_match():
    from horizon.memrouter.router import CONDITION_MISMATCH, CONDITION_UNKNOWN, condition_match
    from horizon.models import Condition as C

    now = [C(key="framework", value="Django"), C(key="rps", value=20000)]
    assert condition_match(now, [C(key="framework", value="django")]) == 1.0
    assert condition_match(now, [C(key="rps", op=">", value=10000)]) == 1.0  # predicate on the stored side
    assert condition_match(now, [C(key="rps", op="<", value=1000)]) == CONDITION_MISMATCH
    assert condition_match(now, [C(key="framework", value="django"), C(key="rps", op="<", value=1000)]) == \
        CONDITION_MISMATCH + (1 - CONDITION_MISMATCH) / 2
    assert condition_match(now, [C(key="db", value="postgres")]) == CONDITION_UNKNOWN  # nothing shared
    assert condition_match([C(key="rps", op=">", value=5)], [C(key="rps", op="<", value=9)]) == CONDITION_UNKNOWN
    assert condition_match([C(key="lang", value="py")], [C(key="lang", op="in", value=["py", "go"])]) == 1.0
    assert condition_match([C(key="rps", value="lots")], [C(key="rps", op=">", value=1)]) == CONDITION_UNKNOWN


def test_recall_ranks_by_conditions_and_prefers_the_same_project(memrouter):
    from horizon.models import Actual, Condition as C

    common = dict(team_id="local", situation="choose a rate limiting approach", actual=Actual(success=1.0))
    memrouter.record(chosen="redis limiter", conditions=[C(key="rps", op=">", value=10000)], **common)
    memrouter.record(chosen="in-memory limiter", conditions=[C(key="rps", op="<", value=1000)], **common)
    for rps, first in ((50000, "redis limiter"), (500, "in-memory limiter")):
        got = memrouter.recall(team_id="local", situation="choose a rate limiting approach",
                               conditions=[C(key="rps", value=rps)])
        assert got.memories[0].chosen == first

    memrouter.record(chosen="other project's pick", project_id="b", team_id="local",
                     situation="pick a csv library", actual=Actual(success=1.0))
    memrouter.record(chosen="this project's pick", project_id="a", team_id="local",
                     situation="pick a csv library", actual=Actual(success=1.0))
    for project, first in (("a", "this project's pick"), ("b", "other project's pick")):
        got = memrouter.recall(team_id="local", situation="pick a csv library", project_id=project)
        assert got.memories[0].chosen == first
