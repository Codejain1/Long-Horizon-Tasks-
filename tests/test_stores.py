from datetime import timedelta

import numpy as np

from horizon.memrouter.embedding import HashEmbedder
from horizon.models import (
    Actual,
    Episode,
    OptionRef,
    Predicted,
    ProgressEntry,
    Scope,
    TaskState,
    TestCapture,
    now,
)

EMB = HashEmbedder(384)


def make_episode(situation: str, team: str = "local", success: float = 1.0, **kw) -> Episode:
    return Episode(scope=Scope(team_id=team), situation=situation, chosen=OptionRef(label="x"),
                   predicted=Predicted(), actual=Actual(success=success), surprise=0.0,
                   embedding_model=EMB.model_name, **kw)


def vec(text: str) -> np.ndarray:
    return EMB.embed([text])[0]


# --- task state -----------------------------------------------------------------

def test_task_round_trip_keeps_goal_verbatim(task_store):
    goal = "  Add OAuth login — keep the *exact* wording, please!\n"
    task = task_store.create(TaskState(team_id="local", goal=goal, constraints=["no new deps"], cwd="/p"))
    loaded = task_store.get(task.id, "local")
    assert loaded.goal == goal
    assert loaded.constraints == ["no new deps"]

    loaded.progress.append(ProgressEntry(note="did a thing"))
    loaded.status = "completed"
    task_store.save(loaded)
    again = task_store.get(task.id, "local")
    assert again.progress[0].note == "did a thing"
    assert again.status == "completed"


def test_tasks_are_isolated_per_team(task_store):
    task = task_store.create(TaskState(team_id="team-a", goal="g"))
    assert task_store.get(task.id, "team-b") is None


def test_active_tasks_filter_by_cwd_and_status(task_store):
    a = task_store.create(TaskState(team_id="local", goal="a", cwd="/p1"))
    task_store.create(TaskState(team_id="local", goal="b", cwd="/p2"))
    done = task_store.create(TaskState(team_id="local", goal="c", cwd="/p1"))
    done.status = "completed"
    task_store.save(done)
    assert [t.id for t in task_store.active("local", cwd="/p1")] == [a.id]


def test_captures_pending_and_consumed(task_store):
    old = TestCapture(cwd="/p", command="pytest", runner="pytest", passed=1, failed=0, total=1,
                      created_at=now() - timedelta(hours=1))
    new = TestCapture(cwd="/p", command="pytest", runner="pytest", passed=3, failed=1, total=4)
    task_store.add_capture(old)
    task_store.add_capture(new)
    task_store.add_capture(TestCapture(cwd="/other", command="pytest", runner="pytest", passed=1, failed=0, total=1))

    pending = task_store.pending_captures("/p")
    assert [c.id for c in pending] == [new.id, old.id]  # newest first
    assert [c.id for c in task_store.pending_captures("/p", since=now() - timedelta(minutes=5))] == [new.id]

    task_store.consume_captures([new.id], "ep_1")
    assert [c.id for c in task_store.pending_captures("/p")] == [old.id]


def test_stats(task_store):
    task_store.log_call("start_task", "t1", ok=True)
    task_store.log_call("record_outcome", "t1", ok=False, error="boom")
    cap = task_store.add_capture(TestCapture(cwd="/p", command="pytest", runner="pytest", passed=1, failed=0, total=1))
    task_store.add_capture(TestCapture(cwd="/p", command="pytest", runner="pytest", passed=1, failed=0, total=1))
    task_store.consume_captures([cap.id], "ep")
    stats = task_store.stats()
    assert stats["tool_calls"]["start_task"] == {"calls": 1, "errors": 0}
    assert stats["tool_calls"]["record_outcome"] == {"calls": 1, "errors": 1}
    assert stats["test_runs_captured"] == 2
    assert stats["outcome_recording_rate"] == 0.5


# --- episodes -------------------------------------------------------------------

def test_episode_round_trip(episode_store):
    ep = make_episode("choose an ORM for a django app")
    episode_store.add(ep, vec(ep.situation))
    loaded = episode_store.get(ep.id, "local")
    assert loaded == ep
    assert episode_store.count("local") == 1


def test_search_ranks_by_similarity(episode_store):
    texts = ["pick a database for a django web app",
             "fix flaky websocket reconnect in the react client",
             "choose a postgres driver for the django backend"]
    for t in texts:
        episode_store.add(make_episode(t), vec(t))
    hits = episode_store.search("local", vec("which database driver for django"), EMB.model_name, k=3)
    assert [e.situation for e, _ in hits][-1] == texts[1]
    sims = [s for _, s in hits]
    assert sims == sorted(sims, reverse=True)


def test_search_respects_team_archive_model_and_k(episode_store):
    t = "choose a queue library"
    episode_store.add(make_episode(t, team="other"), vec(t))
    episode_store.add(make_episode(t, archived_at=now()), vec(t))
    other_model = make_episode(t)
    other_model.embedding_model = "some-other-model"
    episode_store.add(other_model, vec(t))
    for _ in range(3):
        episode_store.add(make_episode(t), vec(t))

    hits = episode_store.search("local", vec(t), EMB.model_name, k=2)
    assert len(hits) == 2
    assert all(e.scope.team_id == "local" and e.archived_at is None for e, _ in hits)
    assert all(e.embedding_model == EMB.model_name for e, _ in hits)


def test_recall_log(episode_store):
    episode_store.log_recall("rc_1", "local", "t1", "situation", ["ep_a", "ep_b"])
    assert episode_store.get_recall("rc_1") == ["ep_a", "ep_b"]
