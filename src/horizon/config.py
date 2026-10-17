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
DEFAULT_DECISION_WEIGHTS = {"success": 0.25, "compatibility": 0.15, "architecture_fit": 0.10,
                            "future_fit": 0.20, "efficiency": 0.10,  # Jev's view of the work to come, and of cost
                            "no_repeat": 0.30,  # with decision_history: won't hit what followed similar choices
                            "cost_usd": 0.10, "tokens": 0.05, "latency_ms": 0.05,  # the host's estimates, if any
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
    # "own": our own judge on any OpenAI-compatible endpoint, calibrated on real outcomes (decision/scorers.py)
    own_judge_url: str = ""
    own_judge_model: str = "local"
    decision_weights: dict = field(default_factory=lambda: dict(DEFAULT_DECISION_WEIGHTS))
    crucial_threshold: float = 0.5  # any crucial signal at or above this makes the decision crucial
    clear_margin: float = 0.10  # composite lead over the runner-up that counts as a clear winner
    min_confidence: float = 0.5  # Jev Score confidence below this is never a clear winner
    high_stakes_threshold: float = 0.5  # spending money, messaging people, deleting data: ask a human
    # Consequence checking (PROJECT.md §6).
    spike_reuse_similarity: float = 0.85  # a past spike this similar (same option label) is reused
    # With a relevant lesson from another project, a pick is only taken without a check when Jev is this sure it
    # won't repeat it (no_repeat); in between, the agent spikes the lesson's trouble first.
    lesson_safe: float = 0.7
    try_reversible_threshold: float = 0.7  # every close option at least this reversible: try and roll back
    spike_budget_minutes: int = 10  # per spike, told to the host
    # World model (PROJECT.md §6). "auto": forecasts are logged with every decision and used only once the
    # replay gate shows they beat the stand-ins; "shadow": logged, never used; "on": always used; "off".
    world_model: str = "auto"
    world_model_class: str = ""  # "package.module:Class" to plug in another model, built as Class(router, settings)
    world_model_min_similarity: float = 0.5  # past episodes less similar than this don't count as evidence
    world_model_min_pairs: int = 30  # paired outcomes per stand-in before the gate can pass
    world_model_settle_confidence: float = 0.6  # forecast confidence to settle a close call without spikes
    world_model_gate_ttl_s: int = 600  # how long a gate result is reused before the replay runs again
    # A session with no event for this long is over even without SessionEnd (a crash, a killed terminal).
    session_idle_minutes: int = 120
    # "on": when a lean task starts, show what similar past sessions taught (v2 Phase 1). Off until it passes
    # the evaluation gate (docs/EVALS.md); the evals' candidate arm turns it on.
    session_memory: str = "off"
    # Unasked-for context must be clearly relevant: fastembed puts related tasks at 0.87+, unrelated ones at
    # ~0.72 (the hash embedder's scale is lower: 0.56+ against ~0.39).
    session_memory_min_similarity: float = 0.8
    # "composer": lean's context is chosen by the context composer (v2 Phase 1 track D) instead of the classic
    # blocks. Classic until it passes the evaluation gate; the evals' candidate arm turns it on.
    context: str = "classic"
    context_budget_chars: int = 4000  # the session-start block's ceiling, about 1,000 tokens
    # The past-sessions note shows only when the session forecast expects at least this many failing runs.
    context_min_failed_runs: float = 0.5
    # "on": lean warns a session stuck on the same failing check (v2 Phase 2). Off until it passes the gate.
    stall: str = "off"
    stall_min_runs: int = 4  # never fewer consecutive stuck runs than this, whatever the forecast says
    # "on": lean warns before a command like one the permissions already refused twice, and lists at session start
    # what earlier sessions had refused (`horizon.deadends`). Refusals are logged either way. Off until the gate.
    dead_ends: str = "off"
    decide: str = "off"  # "on": lean triggers evaluate_options itself on tasks with a crucial choice
    # What a session episode's surprise is measured against (v2 Phase 2): "forecast" (the session forecast),
    # "fallback" (similar episodes' success, MEMROUTER.md §5), or "auto": the forecast once its replay is clearly
    # better than the fallback over `world_model_min_pairs` sessions.
    session_surprise: str = "auto"

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
            session_idle_minutes=get("SESSION_IDLE_MINUTES", base.session_idle_minutes, int),
            session_memory=get("SESSION_MEMORY", base.session_memory),
            session_memory_min_similarity=get("SESSION_MEMORY_MIN_SIMILARITY", base.session_memory_min_similarity,
                                              float),
            context=get("CONTEXT", base.context),
            context_budget_chars=get("CONTEXT_BUDGET_CHARS", base.context_budget_chars, int),
            context_min_failed_runs=get("CONTEXT_MIN_FAILED_RUNS", base.context_min_failed_runs, float),
            stall=get("STALL", base.stall),
            stall_min_runs=get("STALL_MIN_RUNS", base.stall_min_runs, int),
            dead_ends=get("DEAD_ENDS", base.dead_ends),
            decide=get("DECIDE", base.decide),
            session_surprise=get("SESSION_SURPRISE", base.session_surprise),
            scorer=get("SCORER", base.scorer),
            jev_model=get("JEV_MODEL", base.jev_model),
            llm_scorer_model=get("LLM_SCORER_MODEL", base.llm_scorer_model),
            own_judge_url=get("OWN_JUDGE_URL", base.own_judge_url),
            own_judge_model=get("OWN_JUDGE_MODEL", base.own_judge_model),
            decision_weights=get("DECISION_WEIGHTS", base.decision_weights, json.loads),
            crucial_threshold=get("CRUCIAL_THRESHOLD", base.crucial_threshold, float),
            clear_margin=get("CLEAR_MARGIN", base.clear_margin, float),
            min_confidence=get("MIN_CONFIDENCE", base.min_confidence, float),
            high_stakes_threshold=get("HIGH_STAKES_THRESHOLD", base.high_stakes_threshold, float),
            spike_reuse_similarity=get("SPIKE_REUSE_SIMILARITY", base.spike_reuse_similarity, float),
            try_reversible_threshold=get("TRY_REVERSIBLE_THRESHOLD", base.try_reversible_threshold, float),
            spike_budget_minutes=get("SPIKE_BUDGET_MINUTES", base.spike_budget_minutes, int),
            world_model=get("WORLD_MODEL", base.world_model),
            world_model_class=get("WORLD_MODEL_CLASS", base.world_model_class),
            world_model_min_similarity=get("WORLD_MODEL_MIN_SIMILARITY", base.world_model_min_similarity, float),
            world_model_min_pairs=get("WORLD_MODEL_MIN_PAIRS", base.world_model_min_pairs, int),
            world_model_settle_confidence=get("WORLD_MODEL_SETTLE_CONFIDENCE", base.world_model_settle_confidence,
                                              float),
            world_model_gate_ttl_s=get("WORLD_MODEL_GATE_TTL_S", base.world_model_gate_ttl_s, int),
        )
