import dataclasses
import os

import pytest

from horizon.config import Settings
from horizon.db import connect
from horizon.memrouter.embedding import HashEmbedder
from horizon.memrouter.router import MemRouter
from horizon.memrouter.store import EpisodeStore
from horizon.service import Platform
from horizon.taskstate.store import TaskStore

PG_URL = os.environ.get("HORIZON_TEST_PG_URL")
TABLES = ("tasks", "test_captures", "tool_calls", "checkpoints", "decisions", "episodes", "recall_log", "spike_results",
          "lessons", "links", "memory_state", "predictor_stats", "consolidation_runs", "episodes_archive")


def _reset_postgres(url: str) -> None:
    db = connect(url)
    for table in TABLES:
        db.execute(f"DROP TABLE IF EXISTS {table}")
    db.close()


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'horizon.db'}"
    if not PG_URL:
        pytest.skip("HORIZON_TEST_PG_URL not set")
    _reset_postgres(PG_URL)
    return PG_URL


@pytest.fixture
def settings(db_url):
    return dataclasses.replace(Settings(), db_url=db_url, embedder="hash")


@pytest.fixture
def task_store(settings):
    db = connect(settings.db_url)
    yield TaskStore(db)
    db.close()


@pytest.fixture
def episode_store(settings):
    db = connect(settings.db_url)
    yield EpisodeStore(db, settings.embedding_dim)
    db.close()


@pytest.fixture
def memrouter(episode_store, settings):
    return MemRouter(episode_store, HashEmbedder(settings.embedding_dim), settings)


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "project"
    d.mkdir()
    return str(d.resolve())


@pytest.fixture
def platform(settings, task_store, memrouter, project_dir):
    return Platform(settings, task_store, lambda: memrouter, cwd=project_dir)
