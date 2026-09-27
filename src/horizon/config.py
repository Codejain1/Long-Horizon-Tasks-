"""Settings, read from environment variables (prefix ``HORIZON_``).

Defaults follow MEMROUTER.md §14. Everything here is easy to change later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def default_db_url() -> str:
    return f"sqlite:///{Path.home() / '.horizon' / 'horizon.db'}"


@dataclass(frozen=True)
class SurpriseWeights:
    success: float = 0.6
    efficiency: float = 0.4


@dataclass(frozen=True)
class Settings:
    # "sqlite:///path/to.db" for local dev, "postgresql://..." for Postgres + pgvector.
    db_url: str = field(default_factory=default_db_url)
    # Single team until API keys arrive in Phase 7; all memory is scoped to it.
    team_id: str = "local"
    # "fastembed" (default, BAAI/bge-small-en-v1.5) or "hash" (offline, deterministic).
    embedder: str = "fastembed"
    embedding_dim: int = 384
    surprise_weights: SurpriseWeights = field(default_factory=SurpriseWeights)
    surprise_epsilon: float = 1e-9
    fallback_success_probability: float = 0.5
    # Episodes at or above this similarity count as "similar" for the fallback success rate.
    similar_min_similarity: float = 0.5
    similar_top_k: int = 10
    recall_top_k: int = 50
    recall_min_similarity: float = 0.2
    recall_max_items: int = 8
    recall_token_budget: int = 1500
    # Static key for the HTTP transport until real API keys (Phase 7).
    dev_api_key: str | None = None
    # Rollback rules (PROJECT.md §8): an outcome below this pass rate is a failure, and this many
    # consecutive failures escalate the task to a human.
    rollback_below: float = 1.0
    max_attempts: int = 3

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)

        def get(name: str, default, cast=str):
            raw = env.get(f"HORIZON_{name}")
            return default if raw in (None, "") else cast(raw)

        base = cls()
        return cls(
            db_url=get("DB_URL", base.db_url),
            team_id=get("TEAM_ID", base.team_id),
            embedder=get("EMBEDDER", base.embedder),
            embedding_dim=get("EMBEDDING_DIM", base.embedding_dim, int),
            surprise_weights=SurpriseWeights(
                success=get("SURPRISE_W_SUCCESS", base.surprise_weights.success, float),
                efficiency=get("SURPRISE_W_EFFICIENCY", base.surprise_weights.efficiency, float),
            ),
            surprise_epsilon=get("SURPRISE_EPSILON", base.surprise_epsilon, float),
            fallback_success_probability=get("FALLBACK_SUCCESS", base.fallback_success_probability, float),
            similar_min_similarity=get("SIMILAR_MIN_SIMILARITY", base.similar_min_similarity, float),
            similar_top_k=get("SIMILAR_TOP_K", base.similar_top_k, int),
            recall_top_k=get("RECALL_TOP_K", base.recall_top_k, int),
            recall_min_similarity=get("RECALL_MIN_SIMILARITY", base.recall_min_similarity, float),
            recall_max_items=get("RECALL_MAX_ITEMS", base.recall_max_items, int),
            recall_token_budget=get("RECALL_TOKEN_BUDGET", base.recall_token_budget, int),
            dev_api_key=get("DEV_API_KEY", None),
            rollback_below=get("ROLLBACK_BELOW", base.rollback_below, float),
            max_attempts=get("MAX_ATTEMPTS", base.max_attempts, int),
        )
