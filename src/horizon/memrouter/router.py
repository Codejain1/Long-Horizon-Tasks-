"""Memrouter build step 1: episodes, write path and basic similarity recall.

Later steps (links, spreading activation, attention filter, decay,
consolidation, fear memories, predictor trust) arrive in Phase 6.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from horizon.config import Settings
from horizon.memrouter.embedding import Embedder
from horizon.memrouter.store import EpisodeStore
from horizon.memrouter.surprise import SurpriseResult, compute_surprise
from horizon.models import (
    Actual,
    Condition,
    Episode,
    MemoryItem,
    OptionRef,
    Predicted,
    Provenance,
    Scope,
    Severity,
    episode_text,
    new_id,
)


@dataclass
class ContextSlice:
    recall_id: str
    memories: list[MemoryItem]
    candidates: int  # how many similar episodes were considered
    truncated: bool  # True when the token budget or item cap dropped some


@dataclass
class RecordResult:
    episode: Episode
    surprise: SurpriseResult
    predicted_success_used: float


def estimate_tokens(text: str) -> int:
    """Rough token count (≈4 characters per token); good enough for a budget cap."""
    return max(1, len(text) // 4)


def describe_outcome(ep: Episode) -> str:
    tr = ep.actual.test_results
    tests = f"tests {tr.passed}/{tr.total} passed" if tr else f"success {ep.actual.success:.0%}"
    if abs(ep.surprise) < 0.1:
        vs = "as predicted"
    else:
        vs = "better than predicted" if ep.surprise > 0 else "worse than predicted"
    return f"{tests}, {vs}"


class MemRouter:
    def __init__(self, store: EpisodeStore, embedder: Embedder, settings: Settings):
        if embedder.dim != store.dim:
            raise ValueError(f"embedder dim {embedder.dim} != store dim {store.dim}")
        self.store = store
        self.embedder = embedder
        self.settings = settings

    def _embed(self, text: str):
        return self.embedder.embed([text])[0]

    # --- write path (MEMROUTER.md §5, steps 1-2) -------------------------------

    def similar_success_rate(self, team_id: str, text: str) -> float | None:
        """Historical success rate of similar, non-archived episodes (None if there are none)."""
        hits = self.store.search(team_id, self._embed(text), self.embedder.model_name, self.settings.similar_top_k)
        rates = [ep.actual.success for ep, sim in hits if sim >= self.settings.similar_min_similarity]
        return sum(rates) / len(rates) if rates else None

    def record(
        self,
        *,
        team_id: str,
        situation: str,
        chosen: str,
        actual: Actual,
        predicted: Predicted | None = None,
        alternatives: list[str] | None = None,
        conditions: list[Condition] | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        recall_id: str | None = None,
        severity: Severity = "normal",
    ) -> RecordResult:
        predicted = predicted or Predicted()
        conditions = conditions or []
        text = episode_text(situation, conditions)

        low_confidence = predicted.success is None
        if low_confidence:
            history = self.similar_success_rate(team_id, text)
            p_success = history if history is not None else self.settings.fallback_success_probability
        else:
            p_success = predicted.success

        result = compute_surprise(
            p_success, predicted, actual, self.settings.surprise_weights, self.settings.surprise_epsilon
        )
        episode = Episode(
            scope=Scope(team_id=team_id, project_id=project_id, task_id=task_id),
            situation=situation,
            conditions=conditions,
            chosen=OptionRef(label=chosen),
            alternatives=[OptionRef(label=a) for a in alternatives or []],
            predicted=predicted,
            actual=actual,
            surprise=result.surprise,
            low_confidence=low_confidence,
            severity=severity,
            provenance=Provenance(agent_id=agent_id, task_id=task_id),
            recall_id=recall_id,
            embedding_model=self.embedder.model_name,
        )
        self.store.add(episode, self._embed(text))
        return RecordResult(episode=episode, surprise=result, predicted_success_used=p_success)

    # --- read path (MEMROUTER.md §6, steps 1-2 and 6) --------------------------

    def recall(
        self,
        *,
        team_id: str,
        situation: str,
        conditions: list[Condition] | None = None,
        token_budget: int | None = None,
        task_id: str | None = None,
    ) -> ContextSlice:
        budget = self.settings.recall_token_budget if token_budget is None else token_budget
        text = episode_text(situation, conditions or [])
        hits = self.store.search(team_id, self._embed(text), self.embedder.model_name, self.settings.recall_top_k)
        hits = [(ep, sim) for ep, sim in hits if sim >= self.settings.recall_min_similarity]

        memories: list[MemoryItem] = []
        used = 0
        for ep, sim in hits:
            if len(memories) >= self.settings.recall_max_items:
                break
            item = MemoryItem(
                episode_id=ep.id,
                situation=ep.situation,
                chosen=ep.chosen.label,
                outcome=describe_outcome(ep),
                success=round(ep.actual.success, 3),
                surprise=round(ep.surprise, 3),
                similarity=round(sim, 3),
                conditions=ep.conditions,
                severity=ep.severity,
                recorded_at=ep.provenance.created_at,
            )
            cost = estimate_tokens(json.dumps(item.model_dump(mode="json")))
            if used + cost > budget:
                break
            memories.append(item)
            used += cost

        recall_id = new_id("rc")
        self.store.log_recall(recall_id, team_id, task_id, situation, [m.episode_id for m in memories])
        return ContextSlice(
            recall_id=recall_id,
            memories=memories,
            candidates=len(hits),
            truncated=len(memories) < len(hits),
        )
