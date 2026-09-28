"""Settings, read from environment variables (prefix ``HORIZON_``).

Defaults follow MEMROUTER.md §14. Everything here is easy to change later.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def default_db_url() -> str:
    return f"sqlite:///{Path.home() / '.horizon' / 'horizon.db'}"


# Composite weights (PROJECT.md §5). Judged by the scorer: success, compatibility, architecture_fit.
# Measured in code from the host's estimates: cost_usd, tokens, latency_ms. Renormalised over what's available.
DEFAULT_DECISION_WEIGHTS = {"success": 0.30, "compatibility": 0.20, "architecture_fit": 0.20,
                            "cost_usd": 0.15, "tokens": 0.10, "latency_ms": 0.05,
                            # Second pass only (consequences as evidence, PROJECT.md §6 step 4):
                            "no_regressions": 0.20, "relative_cost": 0.10}


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
    fallback_success_probability: float = 0.5
    # Episodes at or above this similarity count as "similar" for the fallback success rate.
    similar_min_similarity: float = 0.5
    similar_top_k: int = 10
    recall_top_k: int = 50
    recall_min_similarity: float = 0.2
    recall_max_items: int = 8
    recall_token_budget: int = 1500
    # Memrouter learning (MEMROUTER.md §5-§9, §14). Starting points, tune with data.
    learning_rate: float = 0.2
    low_confidence_multiplier: float = 0.5
    default_strength: float = 0.5  # every memory starts here (§8)
    semantic_link_weight: float = 0.1  # new links start weak and must earn strength (§5)
    semantic_link_min_similarity: float = 0.6
    semantic_links_per_memory: int = 3
    activation_hops: int = 2
    activation_decay: float = 0.5  # per hop
    shortlist_size: int = 25  # §14: 20-30 before the attention filter
    other_project_factor: float = 0.85  # memories from another project in the team; 0 = hard project walls
    attention_threshold: float = 0.5  # Jev's relevance Noul needed to enter context
    link_prune_threshold: float = 0.05
    consolidation_every: int = 200  # episodes; 0 = only on `horizon consolidate` (nightly cron)
    lesson_min_evidence: int = 3  # agreeing episodes before the sleep job distils a lesson or strategy
    archive_keep: int = 3  # newest evidence episodes per lesson kept in retrieval; older ones are archived
    export_dir: str = field(default_factory=lambda: str(Path.home() / ".horizon" / "exports"))
    # Static key for the HTTP transport until real API keys (Phase 7).
    dev_api_key: str | None = None
    # Rollback rules (PROJECT.md §8): an outcome below this pass rate is a failure, and this many
    # consecutive failures escalate the task to a human.
    rollback_below: float = 1.0
    max_attempts: int = 3
    # Decision layer (PROJECT.md §5). "none" until the owner enables a scorer: then options are ranked on
    # measured values only. "jev" needs TYPESAFE_API_KEY; "llm" (the small-LLM comparison scorer) needs Claude
    # API credentials.
    scorer: str = "none"
    jev_model: str = "jev-latest"
    llm_scorer_model: str = "claude-haiku-4-5"
    decision_weights: dict = field(default_factory=lambda: dict(DEFAULT_DECISION_WEIGHTS))
    crucial_threshold: float = 0.5  # any crucial signal at or above this makes the decision crucial
    clear_margin: float = 0.10  # composite lead over the runner-up that counts as a clear winner
    min_confidence: float = 0.5  # Jev Score confidence below this is never a clear winner
    high_stakes_threshold: float = 0.5  # spending money, messaging people, deleting data: ask a human
    # Consequence checking (PROJECT.md §6).
    spike_reuse_similarity: float = 0.85  # a past spike this similar (same option label) is reused
    try_reversible_threshold: float = 0.7  # every close option at least this reversible: try and roll back
    spike_budget_minutes: int = 10  # per spike, told to the host

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
            fallback_success_probability=get("FALLBACK_SUCCESS", base.fallback_success_probability, float),
            similar_min_similarity=get("SIMILAR_MIN_SIMILARITY", base.similar_min_similarity, float),
            similar_top_k=get("SIMILAR_TOP_K", base.similar_top_k, int),
            recall_top_k=get("RECALL_TOP_K", base.recall_top_k, int),
            recall_min_similarity=get("RECALL_MIN_SIMILARITY", base.recall_min_similarity, float),
            recall_max_items=get("RECALL_MAX_ITEMS", base.recall_max_items, int),
            recall_token_budget=get("RECALL_TOKEN_BUDGET", base.recall_token_budget, int),
            learning_rate=get("LEARNING_RATE", base.learning_rate, float),
            activation_hops=get("ACTIVATION_HOPS", base.activation_hops, int),
            activation_decay=get("ACTIVATION_DECAY", base.activation_decay, float),
            shortlist_size=get("SHORTLIST_SIZE", base.shortlist_size, int),
            other_project_factor=get("OTHER_PROJECT_FACTOR", base.other_project_factor, float),
            attention_threshold=get("ATTENTION_THRESHOLD", base.attention_threshold, float),
            link_prune_threshold=get("LINK_PRUNE_THRESHOLD", base.link_prune_threshold, float),
            consolidation_every=get("CONSOLIDATION_EVERY", base.consolidation_every, int),
            export_dir=get("EXPORT_DIR", base.export_dir),
            dev_api_key=get("DEV_API_KEY", None),
            rollback_below=get("ROLLBACK_BELOW", base.rollback_below, float),
            max_attempts=get("MAX_ATTEMPTS", base.max_attempts, int),
            scorer=get("SCORER", base.scorer),
            jev_model=get("JEV_MODEL", base.jev_model),
            llm_scorer_model=get("LLM_SCORER_MODEL", base.llm_scorer_model),
            decision_weights=get("DECISION_WEIGHTS", base.decision_weights, json.loads),
            crucial_threshold=get("CRUCIAL_THRESHOLD", base.crucial_threshold, float),
            clear_margin=get("CLEAR_MARGIN", base.clear_margin, float),
            min_confidence=get("MIN_CONFIDENCE", base.min_confidence, float),
            high_stakes_threshold=get("HIGH_STAKES_THRESHOLD", base.high_stakes_threshold, float),
            spike_reuse_similarity=get("SPIKE_REUSE_SIMILARITY", base.spike_reuse_similarity, float),
            try_reversible_threshold=get("TRY_REVERSIBLE_THRESHOLD", base.try_reversible_threshold, float),
            spike_budget_minutes=get("SPIKE_BUDGET_MINUTES", base.spike_budget_minutes, int),
        )
