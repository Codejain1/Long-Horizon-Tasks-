"""Phase 6: memrouter learning (MEMROUTER.md §5-§9)."""

import dataclasses

import pytest

from horizon.decision.scorers import Answer
from horizon.memrouter.embedding import HashEmbedder
from horizon.memrouter.router import MemRouter
from horizon.models import Actual, Condition, Predicted

T = "local"


def router_with(settings, episode_store, **overrides):
    return MemRouter(episode_store, HashEmbedder(settings.embedding_dim),
                     dataclasses.replace(settings, **overrides))


@pytest.fixture
def mr(settings, episode_store):
    return router_with(settings, episode_store, consolidation_every=0)


def rec(mr, situation, chosen, success, predicted=None, recall_id=None, conditions=None, **kw):
    return mr.record(team_id=T, situation=situation, chosen=chosen, actual=Actual(success=success, **kw.pop("actual", {})),
                     predicted=Predicted(success=predicted), recall_id=recall_id, conditions=conditions, **kw).episode


def link(mr, a, b, kind="co-success"):
    return mr.graph.link_weight(a, b, kind)


def strength(mr, node_id):
    return mr.graph.states([node_id])[node_id]


# --- §5 step 3: surprise-based link learning -------------------------------------------

def test_new_memories_get_weak_semantic_links(mr):
    a = rec(mr, "choose a rate limiter for the login api", "token bucket", 1.0, 0.5)
    b = rec(mr, "choose a rate limiter for the signup api", "token bucket", 1.0, 0.5)
    assert link(mr, a.id, b.id, "semantic") == pytest.approx(0.1)  # new links start weak


def test_links_follow_the_sign_and_size_of_surprise(settings, episode_store):
    mr = router_with(settings, episode_store, consolidation_every=0)
    seed = rec(mr, "choose a rate limiter for the login api", "token bucket", 1.0, 0.5)

    def one_decision(success, predicted, signal="auto"):
        rc = mr.recall(team_id=T, situation="choose a rate limiter for the login api")
        ep = mr.record(team_id=T, situation="choose a rate limiter for the login api", chosen="token bucket",
                       actual=Actual(success=success, signal_type=signal), predicted=Predicted(success=predicted),
                       recall_id=rc.recall_id).episode
        return link(mr, seed.id, ep.id)

    better = one_decision(1.0, 0.5)  # surprise +0.5 -> 0.1 + 0.2 x 0.5 x 0.7
    assert better == pytest.approx(0.1 + 0.2 * 0.5 * 0.7)
    expected = one_decision(0.9, 0.9)  # no surprise, no change from the starting weight
    assert expected == pytest.approx(0.1)
    worse = one_decision(0.0, 0.1, signal="human")  # surprise -0.1, human weight 1.0
    assert worse == pytest.approx(0.1 - 0.2 * 0.1 * 1.0)


def test_low_confidence_halves_the_learning_rate(mr):
    seed = rec(mr, "pick a queue for background jobs", "rq", 1.0, 0.5)
    rc = mr.recall(team_id=T, situation="pick a queue for background jobs")
    ep = rec(mr, "pick a queue for background jobs", "rq", 1.0, None, recall_id=rc.recall_id)  # no prediction
    assert ep.low_confidence
    assert link(mr, seed.id, ep.id) == pytest.approx(0.1 + 0.2 * 0.7 * 0.5 * ep.surprise)


# --- §6: spreading activation, attention filter, token budget ------------------------------

def test_spreading_activation_surfaces_linked_memories_that_are_not_similar(mr):
    a = rec(mr, "api latency spikes under load", "add caching", 1.0, 0.5)
    b = rec(mr, "database connection pool exhausted", "raise pool size", 1.0, 0.5)
    mr.graph.set_link(T, a.id, b.id, "co-success", 0.9)  # learned together earlier
    got = mr.recall(team_id=T, situation="api latency spikes under load")
    ids = [m.episode_id for m in got.memories]
    assert ids[0] == a.id and b.id in ids  # b isn't textually similar: it came through the link
    assert next(m for m in got.memories if m.episode_id == b.id).similarity == 0.0

    mr.graph.set_link(T, a.id, b.id, "co-success", 0.0)
    assert b.id not in [m.episode_id for m in mr.recall(team_id=T, situation="api latency spikes under load").memories]


class Relevance:
    name = "jev"

    def __init__(self, keep):
        self.keep, self.calls = keep, 0

    def ask(self, state, questions):
        self.calls += 1
        return {q: Answer(0.9 if self.keep in state["memories"][int(q[1:])]["option"] else 0.1) for q in questions}


def test_attention_filter_decides_what_enters_context(settings, episode_store):
    jev = Relevance(keep="postgres")
    mr = MemRouter(episode_store, HashEmbedder(settings.embedding_dim),
                   dataclasses.replace(settings, consolidation_every=0), attention=lambda: jev)
    rec(mr, "choose a database for orders", "postgres", 1.0, 0.5)
    rec(mr, "choose a database for orders service", "mongodb", 1.0, 0.5)
    got = mr.recall(team_id=T, situation="choose a database for orders")
    assert got.filtered_by == "jev" and [m.chosen for m in got.memories] == ["postgres"] and jev.calls == 1

    def broken():
        raise ConnectionError("529")

    mr._attention = broken  # falls back to the activation order, never blocks
    assert mr.recall(team_id=T, situation="choose a database for orders").filtered_by == "activation"


def test_token_budget_caps_the_slice(mr):
    for i in range(6):
        rec(mr, f"choose a logging library variant {i}", f"lib{i}", 1.0, 0.5)
    small = mr.recall(team_id=T, situation="choose a logging library", token_budget=150)
    assert 1 <= len(small.memories) < 6 and small.truncated


# --- §8: spaced-repetition decay and pruning ---------------------------------------------

def test_helpful_recalls_strengthen_and_stabilise_unhelpful_ones_weaken(mr):
    good = rec(mr, "choose an http client", "httpx", 1.0, 0.5)
    bad = rec(mr, "choose an http client library", "urllib", 1.0, 0.5)
    rc = mr.recall(team_id=T, situation="choose an http client")
    rec(mr, "choose an http client", "httpx", 1.0, 0.6, recall_id=rc.recall_id)  # followed good, it worked
    g, b = strength(mr, good.id), strength(mr, bad.id)
    assert g.strength > 0.5 and g.stability == pytest.approx(1.5) and g.helpful == 1
    assert b.strength == pytest.approx(0.5 - 0.02)  # recalled but not used: an opportunity passed

    rc = mr.recall(team_id=T, situation="choose an http client")
    rec(mr, "choose an http client", "urllib", 0.0, 0.9, recall_id=rc.recall_id)  # followed bad, it failed
    assert strength(mr, bad.id).strength < 0.48 - 0.1


def test_proven_memories_decay_slower_but_never_become_permanent(mr):
    proven = rec(mr, "choose a test runner", "pytest", 1.0, 0.5)
    for _ in range(4):  # four helpful recalls
        rc = mr.recall(team_id=T, situation="choose a test runner")
        rec(mr, "choose a test runner", "pytest", 1.0, 0.6, recall_id=rc.recall_id)
    st = strength(mr, proven.id)
    assert st.stability > 4
    before = st.strength
    rc = mr.recall(team_id=T, situation="choose a test runner")
    rec(mr, "choose a test runner", "pytest", 0.0, 0.9, recall_id=rc.recall_id)  # tools change
    drop = before - strength(mr, proven.id).strength
    assert 0 < drop < 0.2 * 0.7 * 0.9  # weakened, by less than an unproven memory would be


def test_weak_links_are_pruned_by_the_sleep_job(mr):
    a = rec(mr, "choose a cache", "redis", 1.0, 0.5)
    b = rec(mr, "choose a cache backend", "redis", 1.0, 0.5)
    mr.graph.set_link(T, a.id, b.id, "co-success", 0.04)
    report = mr.consolidate(T)
    assert report["links_pruned"] >= 1 and link(mr, a.id, b.id) is None


# --- §7: consolidation ------------------------------------------------------------------

def test_sleep_job_distils_strategies_and_lessons_merges_and_archives(settings, episode_store, tmp_path):
    mr = router_with(settings, episode_store, consolidation_every=0, export_dir=str(tmp_path))
    works = [rec(mr, "choose a migration tool for postgres", "alembic", 1.0, 0.7) for _ in range(4)]
    fails = [rec(mr, "choose a date parsing library", "strptime by hand", 0.0, 0.6) for _ in range(3)]
    report = mr.consolidate(T)
    lessons = {les.option: les for les in mr.graph.lessons(T)}
    assert lessons["alembic"].kind == "strategy" and lessons["alembic"].track_record.successes == 4
    assert set(lessons["alembic"].evidence) == {e.id for e in works}
    assert lessons["strptime by hand"].kind == "lesson" and "failed" in lessons["strptime by hand"].statement
    assert link(mr, works[0].id, lessons["alembic"].id, "derived-from") == pytest.approx(0.5)

    # The oldest evidence is archived (cold storage), still readable, and out of retrieval.
    assert report["archived"] == 1 and mr.graph.archived_count(T) == 1
    assert episode_store.get(works[0].id, T) is not None
    recalled = [m.episode_id for m in mr.recall(team_id=T, situation="choose a migration tool for postgres").memories]
    assert works[0].id not in recalled and lessons["alembic"].id in recalled
    assert report["export"] and report["export"].endswith(".parquet")
    import pyarrow.parquet as pq

    table = pq.read_table(report["export"])
    assert table.num_rows == 7 and sum(table.column("archived").to_pylist()) == 1  # episodes are never lost

    for _ in range(3):
        rec(mr, "choose a migration tool for postgres", "alembic", 1.0, 0.7)
    again = mr.consolidate(T)
    assert again["created"] == [] and lessons["alembic"].id in again["merged"]  # merged, not duplicated
    assert mr.graph.get_lesson(lessons["alembic"].id, T).track_record.successes == 7


def test_sleep_job_runs_on_its_cadence(settings, episode_store):
    mr = router_with(settings, episode_store, consolidation_every=3)
    for _ in range(3):
        rec(mr, "choose a formatter", "black", 1.0, 0.7)
    assert [les.option for les in mr.graph.lessons(T)] == ["black"]


# --- §8: conditions and reconsolidation ----------------------------------------------------

def rps(n):
    return [Condition(key="rps", value=n)]


def test_a_contradicted_lesson_gets_narrower_conditions(mr):
    for n in (1000, 2000, 3000):
        rec(mr, "choose a rate limiter", "in-memory counter", 1.0, 0.7, conditions=rps(n))
    mr.consolidate(T)
    [strategy] = mr.graph.lessons(T)
    rc = mr.recall(team_id=T, situation="choose a rate limiter", conditions=rps(20000))
    assert strategy.id in [m.episode_id for m in rc.memories]
    rec(mr, "choose a rate limiter", "in-memory counter", 0.0, 0.8, conditions=rps(20000), recall_id=rc.recall_id)

    narrowed = mr.graph.get_lesson(strategy.id, T)
    assert [(c.key, c.op, c.value) for c in narrowed.conditions] == [("rps", "<=", 3000.0)]
    assert narrowed.contradictions == 0  # refined, not weakened
    # Now it ranks below where it applies and stays on top where it does.
    high = mr.recall(team_id=T, situation="choose a rate limiter", conditions=rps(20000)).memories
    low = mr.recall(team_id=T, situation="choose a rate limiter", conditions=rps(500)).memories
    assert low[0].episode_id == strategy.id and high[0].episode_id != strategy.id


def test_repeated_contradictions_without_a_condition_difference_weaken(mr):
    for _ in range(3):
        rec(mr, "choose a json library", "orjson", 1.0, 0.7)
    mr.consolidate(T)
    [strategy] = mr.graph.lessons(T)
    before = strength(mr, strategy.id).strength
    for i in range(2):
        rc = mr.recall(team_id=T, situation="choose a json library")
        ep = rec(mr, "choose a json library", "orjson", 0.0, 0.8, recall_id=rc.recall_id)
    les = mr.graph.get_lesson(strategy.id, T)
    assert les.contradictions == 2 and strength(mr, strategy.id).strength < before * 0.6
    assert link(mr, ep.id, strategy.id, "contradicts") == 1.0


# --- §9: fear memories --------------------------------------------------------------------

def test_a_severe_failure_creates_a_fear_that_always_surfaces(settings, episode_store):
    jev = Relevance(keep="nothing")  # the attention filter would drop everything
    mr = MemRouter(episode_store, HashEmbedder(settings.embedding_dim),
                   dataclasses.replace(settings, consolidation_every=0), attention=lambda: jev)
    ep = rec(mr, "migrate the users table", "drop and recreate", 0.0, 0.8, severity="severe",
             conditions=[Condition(key="env", value="production")])
    [fear] = mr.graph.lessons(T, fear_only=True)
    assert fear.evidence == [ep.id] and strength(mr, fear.id).strength == 1.0

    got = mr.recall(team_id=T, situation="migrate the users table", conditions=[Condition(key="env", value="production")],
                    token_budget=1)
    assert [m.kind for m in got.memories] == ["fear"] and got.fear_warnings == 1 and got.memories[0].severity == "severe"
    other = mr.recall(team_id=T, situation="migrate the users table", conditions=[Condition(key="env", value="dev")])
    assert other.fear_warnings == 0  # conditions rule it out


def test_fears_weaken_on_contrary_evidence_and_only_a_human_clears_them(mr):
    rec(mr, "rotate the api keys", "rotate in place", 0.0, 0.8, severity="severe")
    [fear] = mr.graph.lessons(T, fear_only=True)
    rec(mr, "rotate the api keys", "rotate in place", 1.0, 0.5)
    les = mr.graph.get_lesson(fear.id, T)
    assert strength(mr, fear.id).strength == pytest.approx(0.7) and les.is_fear and not les.cleared_by
    assert "contrary evidence" in les.refinements[-1]
    with pytest.raises(ValueError):
        mr.clear_fear(T, fear.id, by_human="")
    mr.clear_fear(T, fear.id, by_human="kartik")
    assert mr.recall(team_id=T, situation="rotate the api keys").fear_warnings == 0


def test_evaluate_options_sees_fear_lessons(settings, task_store, episode_store, project_dir):
    from test_decision import CLOSE, FakeScorer

    from horizon.decision.layer import Option
    from horizon.service import Platform

    mr = router_with(settings, episode_store, consolidation_every=0)
    rec(mr, "choose a migration strategy", "drop and recreate", 0.0, 0.8, severity="severe")
    scorer = FakeScorer(**CLOSE)
    platform = Platform(settings, task_store, lambda: mr, cwd=project_dir, scorer_factory=lambda: scorer)
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, "choose a migration strategy",
                                  [Option("drop and recreate"), Option("online migration")])
    assert any(m["severity"] == "severe" for m in scorer.calls[0][0]["past_outcomes"])  # Jev saw it first
    assert r["decision"] == "check_consequences"


# --- §5 step 4: predictor trust -------------------------------------------------------------

def test_predictor_trust_per_source(mr):
    for p, actual in ((0.9, 1.0), (0.8, 0.0), (0.7, 1.0)):
        mr.record(team_id=T, situation="choose a queue", chosen="rq", actual=Actual(success=actual),
                  predicted=Predicted(success=p, source="jev"), conditions=[Condition(key="task_type", value="infra")])
    rec(mr, "choose a queue", "rq", 1.0, None)  # no prediction: memory's history is the prediction
    [jev] = mr.predictor_trust(T, "jev")
    assert (jev["task_type"], jev["samples"]) == ("infra", 3)
    assert jev["calibration"] == pytest.approx((0.9 + 0.8 + 0.7 - 2) / 3, abs=1e-4)
    assert jev["accuracy"] == pytest.approx(1 - (0.1 + 0.8 + 0.3) / 3, abs=1e-4)
    assert mr.predictor_trust(T, "memory")[0]["samples"] == 1


# --- the headline: retrieval improves over repeated simulated tasks ----------------------------

SITUATION = "choose a caching layer for the product catalog api"


def useful(m) -> bool:
    """Ground truth after the catalog grew: redis works, the in-process dict cache no longer does. A memory is
    useful if it points the agent the right way (redis worked, or dict failed)."""
    return ("redis" in m.chosen) == (m.success >= 0.5)


def simulate(mr, rounds: int) -> list[float]:
    """Old successes with the dict cache sit closest to the query; redis memories are phrased differently.
    Each round an agent recalls, follows the top memory (or avoids it if it failed), and reports the outcome."""
    for _ in range(3):
        rec(mr, SITUATION, "in-process dict cache", 1.0, 0.5)
        rec(mr, "pick caching for catalog api reads", "redis cache-aside", 1.0, 0.5)
    precision = []
    for _ in range(rounds):
        rc = mr.recall(team_id=T, situation=SITUATION)
        top3 = rc.memories[:3]
        precision.append(sum(useful(m) for m in top3) / 3)
        top = rc.memories[0]
        follow = top.success >= 0.5
        pick = top.chosen if follow else next(o for o in ("redis cache-aside", "in-process dict cache")
                                              if o != top.chosen)
        outcome = 1.0 if "redis" in pick else 0.0
        rec(mr, SITUATION, pick, outcome, 0.9 if follow else 0.6, recall_id=rc.recall_id)
    return precision


def test_retrieval_quality_improves_over_repeated_tasks(settings, episode_store):
    learning = simulate(router_with(settings, episode_store, consolidation_every=4), rounds=12)
    assert learning[0] < 0.5  # at first the stale dict successes dominate the top 3
    assert learning[-1] == 1.0 and sum(learning[-4:]) / 4 >= 0.9


def test_without_learning_retrieval_does_not_improve(settings, tmp_path):
    """Same simulation with learning off (Phase 2 memory: similarity only, no links, no decay, no sleep job)."""
    from horizon.db import connect
    from horizon.memrouter.store import EpisodeStore

    store = EpisodeStore(connect(f"sqlite:///{tmp_path / 'static.db'}"), settings.embedding_dim)
    static = simulate(router_with(settings, store, consolidation_every=0, learning_rate=0.0,
                                  activation_hops=0, semantic_links_per_memory=0), rounds=12)
    learned = simulate(router_with(settings, EpisodeStore(connect(f"sqlite:///{tmp_path / 'learn.db'}"),
                                                          settings.embedding_dim), consolidation_every=4), rounds=12)
    assert sum(learned[-4:]) > sum(static[-4:])
