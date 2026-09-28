"""Memrouter (MEMROUTER.md): episodes, the write path with learning, and the routed read path.

Write path (§5): record the episode and its surprise; link it to similar memories; update the links and the
strength of the memories that fed the decision (Hebbian, driven by surprise); update predictor trust; create
a fear lesson on a severe failure; reconsolidate contradicted lessons.
Read path (§6): candidates by similarity x conditionMatch x scope x strength, spreading activation through
learned links, a shortlist, the Jev attention filter within a token budget; fear lessons always surface.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from horizon.config import Settings
from horizon.memrouter.embedding import Embedder
from horizon.memrouter.graph import Graph, State
from horizon.memrouter.spikes import SpikeStore, same_option, spike_text
from horizon.memrouter.store import EpisodeStore
from horizon.memrouter.surprise import SurpriseResult, compute_surprise
from horizon.models import (
    Actual,
    Condition,
    Episode,
    Lesson,
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
    candidates: int  # how many memories were considered after spreading activation
    truncated: bool  # True when the token budget or item cap dropped some
    filtered_by: str = "activation"  # "jev" when the attention filter chose (§6 step 5)
    fear_warnings: int = 0


@dataclass
class RecordResult:
    episode: Episode
    surprise: SurpriseResult
    predicted_success_used: float


# Ranking factors for recall (MEMROUTER.md §6 step 2 and §10). Defaults; tune with data.
CONDITION_MISMATCH = 0.25  # every shared condition contradicts
CONDITION_UNKNOWN = 0.75  # no shared condition that can be compared
OTHER_SCOPE = 0.85  # episode from another project in the team (narrowest scope is preferred)


def _holds(value, op: str, target) -> bool | None:
    """Whether `value` satisfies `op target`; None when it can't be decided."""
    try:
        if op in ("=", "!="):
            same = str(value).strip().lower() == str(target).strip().lower()
            return same if op == "=" else not same
        if op in (">", ">=", "<", "<="):
            a, b = float(value), float(target)
            return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]
        if op == "in":
            return str(value).lower() in [str(t).lower() for t in target] if isinstance(target, list) else None
        if op == "contains":
            return str(target).lower() in str(value).lower()
    except (TypeError, ValueError):
        return None
    return None


def condition_match(query: list[Condition], stored: list[Condition]) -> float:
    """conditionMatch (MEMROUTER.md §6): how well a past episode's conditions fit the current ones.

    Current conditions are usually facts ("framework = django", "rps = 20000"); stored ones may be
    predicates ("rps > 10000"). A key present on both sides is compared when one side is a plain "="
    fact; two predicates on the same key can't be compared and are skipped.
    Returns 1.0 when every comparable key matches, CONDITION_MISMATCH when none do, and
    CONDITION_UNKNOWN when nothing is comparable.
    """
    results = []
    for q in query:
        for s in stored:
            if q.key.lower() != s.key.lower():
                continue
            if q.op == "=":
                results.append(_holds(q.value, s.op, s.value))
            elif s.op == "=":
                results.append(_holds(s.value, q.op, q.value))
    results = [r for r in results if r is not None]
    if not results:
        return CONDITION_UNKNOWN
    return CONDITION_MISMATCH + (1 - CONDITION_MISMATCH) * sum(results) / len(results)


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


log = logging.getLogger(__name__)

SIGNAL_WEIGHTS = {"human": 1.0, "auto": 0.7, "implicit": 0.4}  # MEMROUTER.md §5, §14
MAX_STABILITY = 20.0  # proven memories decay very slowly but are never permanent (§8)
STABILITY_GROWTH = 1.5  # each helpful recall slows future decay
OPPORTUNITY_DECAY = 0.02  # recalled but not used: a small loss, divided by stability (§8 "usage opportunities")
FEAR_SIMILARITY = 0.5  # fear lessons this similar (and not ruled out by conditions) always surface (§9)
CONTRADICTION_LIMIT = 2  # same-condition contradictions before a lesson is weakened (§8)


def task_type(conditions: list[Condition]) -> str:
    """PredictorStats.taskType: a `task_type` condition if the host gave one, else "general"."""
    return next((str(c.value) for c in conditions if c.key == "task_type"), "general")


@dataclass
class Node:
    id: str
    kind: str  # episode | lesson | strategy | fear
    obj: Episode | Lesson
    similarity: float = 0.0

    @property
    def option(self) -> str:
        return self.obj.chosen.label if isinstance(self.obj, Episode) else self.obj.option

    @property
    def conditions(self) -> list[Condition]:
        return self.obj.conditions

    @property
    def project_id(self) -> str | None:
        return self.obj.scope.project_id


class MemRouter:
    def __init__(self, store: EpisodeStore, embedder: Embedder, settings: Settings,
                 attention: Callable[[], object] | None = None):
        if embedder.dim != store.dim:
            raise ValueError(f"embedder dim {embedder.dim} != store dim {store.dim}")
        self.store = store
        self.embedder = embedder
        self.settings = settings
        self.spikes = SpikeStore(store.db, store.dim)
        self.graph = Graph(store.db, store.dim, settings.default_strength)
        self._attention = attention  # returns a Scorer (Jev) or None: the §6 step 5 attention filter

    def _embed(self, text: str):
        return self.embedder.embed([text])[0]

    # --- write path (MEMROUTER.md §5) -----------------------------------------------

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
        vector = self._embed(text)

        # Step 2: surprise, with the low-confidence fallback on the history of similar episodes.
        low_confidence = predicted.success is None
        history = self.similar_success_rate(team_id, text) if low_confidence else None
        if low_confidence:
            p_success = history if history is not None else self.settings.fallback_success_probability
        else:
            p_success = predicted.success
        result = compute_surprise(p_success, predicted, actual, self.settings.surprise_weights)

        # Step 1: the episode. Its neighbours are found before it's stored, so it doesn't match itself.
        neighbours = self._similar_nodes(team_id, vector)
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
        self.store.add(episode, vector)
        # New semantic links start weak and must earn strength through outcomes (§5).
        for node_id, _ in neighbours:
            if self.graph.link_weight(episode.id, node_id, "semantic") is None:
                self.graph.set_link(team_id, episode.id, node_id, "semantic", self.settings.semantic_link_weight)

        self._learn(team_id, episode, recall_id)  # step 3 (+ decay, §8)
        self._update_predictors(team_id, episode, p_success, history)  # step 4
        if severity == "severe":  # step 5
            self._fear(episode, vector)
        self._weaken_fears(team_id, episode)  # contrary evidence (§9)
        self._maybe_consolidate(team_id)
        return RecordResult(episode=episode, surprise=result, predicted_success_used=p_success)

    def _similar_nodes(self, team_id: str, vector) -> list[tuple[str, float]]:
        model, k, floor = self.embedder.model_name, self.settings.semantic_links_per_memory, \
            self.settings.semantic_link_min_similarity
        hits = [(e.id, s) for e, s in self.store.search(team_id, vector, model, k)]
        hits += [(les.id, s) for les, s in self.graph.search_lessons(team_id, vector, model, k)]
        return sorted([h for h in hits if h[1] >= floor], key=lambda h: -h[1])[:k]

    def _nodes(self, team_id: str, ids: list[str]) -> dict[str, Node]:
        episodes = self.store.get_many([i for i in ids if i.startswith("ep_")], team_id)
        out = {i: Node(i, "episode", e) for i, e in episodes.items()}
        for i in ids:
            if not i.startswith("ep_"):
                les = self.graph.get_lesson(i, team_id)
                if les is not None:
                    out[i] = Node(i, "fear" if les.is_fear and not les.cleared_by else les.kind, les)
        return out

    def _learn(self, team_id: str, episode: Episode, recall_id: str | None) -> None:
        """§5 step 3 and §8: the memories recalled for this decision learn from its outcome.

        Credit goes to the memories that fed the decision: the ones about the option the host implemented.
        Their links to each other and to the new episode change by lr x surprise x signalWeight (x 0.5 when
        low-confidence); their strength moves the same way, and a helpful recall also grows their stability
        (spaced repetition). Recalled memories about other options had a usage opportunity and didn't help:
        they lose a little strength, less the more stable they are.
        """
        recalled = self.store.get_recall(recall_id) if recall_id else None
        if not recalled:
            return
        nodes = self._nodes(team_id, recalled)
        fed = [n for n in nodes.values() if same_option(n.option, episode.chosen.label)]
        rate = self.settings.learning_rate * SIGNAL_WEIGHTS[episode.actual.signal_type] * \
            (self.settings.low_confidence_multiplier if episode.low_confidence else 1.0)
        delta = rate * episode.surprise
        helpful = episode.surprise >= 0 and episode.actual.success >= 0.5
        states = self.graph.states(list(nodes))
        fed_ids = {n.id for n in fed}
        for n in nodes.values():
            st = states[n.id]
            st.recalls += 1
            if n.id in fed_ids:
                # A worse-than-predicted outcome weakens a memory less the more proven it is.
                st.strength += delta if delta >= 0 else delta / max(1.0, st.stability ** 0.5)
                if helpful:
                    st.helpful += 1
                    st.stability = min(MAX_STABILITY, st.stability * STABILITY_GROWTH)
            else:
                st.strength -= OPPORTUNITY_DECAY / st.stability
            self.graph.set_state(n.id, team_id, st)
        # Hebbian co-success links among the memories that fed the decision and the new episode.
        members = [n.id for n in fed] + [episode.id]
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                w = self.graph.link_weight(a, b, "co-success")
                w = self.settings.semantic_link_weight if w is None else w
                self.graph.set_link(team_id, a, b, "co-success", w + delta)
        for n in fed:
            if n.kind in ("lesson", "strategy"):
                self._reconsolidate(team_id, n.obj, episode)

    def _update_predictors(self, team_id: str, episode: Episode, p_success: float, history: float | None) -> None:
        """§5 step 4: were the predictions right? The fallback from similar episodes is memory's prediction."""
        kind = task_type(episode.conditions)
        if not episode.low_confidence:
            self.graph.update_predictor(team_id, episode.predicted.source, kind, p_success, episode.actual.success)
        elif history is not None:
            self.graph.update_predictor(team_id, "memory", kind, history, episode.actual.success)

    def predictor_trust(self, team_id: str, source: str | None = None, kind: str | None = None) -> list[dict]:
        """MEMROUTER §12 predictorTrust: calibration, accuracy and samples per source and task type."""
        return [s for s in self.graph.predictor_stats(team_id)
                if (source is None or s["source"] == source) and (kind is None or s["task_type"] == kind)]

    # --- fear memories (§9) ------------------------------------------------------------

    def _fear(self, episode: Episode, vector) -> Lesson:
        """One severe failure creates a strong warning immediately."""
        lesson = Lesson(scope=episode.scope.model_copy(update={"task_id": None}), kind="lesson", is_fear=True,
                        statement=f"Severe failure: {episode.chosen.label} for \"{episode.situation}\"",
                        situation=episode.situation, option=episode.chosen.label, conditions=episode.conditions,
                        evidence=[episode.id], track_record={"failures": 1})
        self.graph.add_lesson(lesson, self.embedder.model_name, vector, strength=1.0, stability=MAX_STABILITY)
        self.graph.set_link(episode.scope.team_id, episode.id, lesson.id, "derived-from", 1.0)
        return lesson

    def _weaken_fears(self, team_id: str, episode: Episode) -> None:
        """Contrary evidence weakens a fear lesson, visibly; only a human clears one (§9)."""
        if episode.actual.success < 0.8 or episode.severity == "severe":
            return
        for les in self.graph.lessons(team_id, fear_only=True):
            if les.cleared_by or not same_option(les.option, episode.chosen.label):
                continue
            if condition_match(episode.conditions, les.conditions) <= CONDITION_MISMATCH:
                continue  # different conditions: not contrary evidence
            st = self.graph.states([les.id])[les.id]
            st.strength *= 0.7
            self.graph.set_state(les.id, team_id, st)
            les.refinements.append(f"weakened by contrary evidence {episode.id}")
            les.track_record.successes += 1
            self.graph.save_lesson(les)

    def clear_fear(self, team_id: str, lesson_id: str, by_human: str) -> Lesson:
        """clearFear(lessonId, byHuman): human only, never silent (MEMROUTER §9, §12)."""
        if not by_human:
            raise ValueError("a fear lesson can only be cleared by a named human")
        les = self.graph.get_lesson(lesson_id, team_id)
        if les is None or not les.is_fear:
            raise ValueError(f"no fear lesson {lesson_id!r}")
        les.cleared_by = by_human
        les.refinements.append(f"cleared by {by_human}")
        self.graph.save_lesson(les)
        self.graph.set_state(les.id, team_id, State(strength=0.1, stability=1.0))
        return les

    # --- inspection (PROJECT.md §10: show, delete; MEMROUTER §11 provenance) ---------------------

    def inspect(self, team_id: str, query: str | None = None, kinds: list[str] | None = None,
                limit: int = 20) -> list[dict]:
        """Memories as the user sees them. With a query, the most similar first; this is not a recall, so
        looking doesn't change what the memory learns."""
        model = self.embedder.model_name
        if query:
            vector = self._embed(query)
            pairs = [(Node(e.id, "episode", e, s)) for e, s in self.store.search(team_id, vector, model, limit)]
            pairs += [Node(les.id, "fear" if les.is_fear and not les.cleared_by else les.kind, les, s)
                      for les, s in self.graph.search_lessons(team_id, vector, model, limit)]
            pairs.sort(key=lambda n: -n.similarity)
        else:
            pairs = [Node(les.id, "fear" if les.is_fear and not les.cleared_by else les.kind, les)
                     for les in self.graph.lessons(team_id)]
            pairs += [Node(e.id, "episode", e) for e in self.store.recent(team_id, limit)]
        if kinds:
            pairs = [n for n in pairs if n.kind in kinds]
        pairs = pairs[:limit]
        states = self.graph.states([n.id for n in pairs])
        out = []
        for n in pairs:
            st = states[n.id]
            item = self._item(n, st, 0.0).model_dump(mode="json")
            item.pop("activation")
            item["memory_id"] = item.pop("episode_id")
            item.update(stability=round(st.stability, 2), recalls=st.recalls, helpful_recalls=st.helpful,
                        provenance=(n.obj.provenance.model_dump(mode="json") if isinstance(n.obj, Episode)
                                    else {"evidence": n.obj.evidence[:10], "refinements": n.obj.refinements,
                                          "cleared_by": n.obj.cleared_by}))
            out.append(item)
        return out

    def remove(self, team_id: str, memory_id: str, reason: str, removed_by: str | None) -> dict:
        """Take a bad memory out of retrieval before it spreads. Episodes are archived, never deleted (they stay
        world-model data); lessons and strategies are deleted. Either way the links go and the removal is
        logged with a snapshot. Fear lessons are cleared, not deleted (§9)."""
        if memory_id.startswith("ep_"):
            ep = self.store.get(memory_id, team_id)
            if ep is None:
                raise ValueError(f"No memory {memory_id!r}.")
            links = self.graph.unlink(memory_id)
            self.graph.archive_episode(memory_id)
            self.graph.log_removal(team_id, memory_id, "episode", ep.model_dump(mode="json"), reason, removed_by)
            return {"memory_id": memory_id, "kind": "episode", "action": "archived", "links_removed": links}
        les = self.graph.get_lesson(memory_id, team_id)
        if les is None:
            raise ValueError(f"No memory {memory_id!r}.")
        if les.is_fear and not les.cleared_by:
            raise ValueError("That is a fear lesson: a human clears it with clear_fear instead.")
        links = self.graph.unlink(memory_id)
        self.graph.delete_lesson(memory_id)
        self.graph.log_removal(team_id, memory_id, les.kind, les.model_dump(mode="json"), reason, removed_by)
        return {"memory_id": memory_id, "kind": les.kind, "action": "deleted", "links_removed": links}

    # --- reconsolidation (§8) ------------------------------------------------------------

    def _reconsolidate(self, team_id: str, les: Lesson, episode: Episode) -> None:
        """A contradicted lesson first gets narrower conditions; weakened only if contradictions repeat."""
        works = episode.actual.success >= 0.5
        if les.is_fear:
            return
        if (les.kind == "strategy") == works:  # confirmed
            if works:
                les.track_record.successes += 1
            else:
                les.track_record.failures += 1
            les.track_record.human_weighted += (2 if episode.actual.signal_type == "human" else 1) * (1 if works else -1)
            les.evidence.append(episode.id)
            self.graph.save_lesson(les)
            return
        refined = self._distinguishing_condition(team_id, les, episode)
        if refined is not None:
            les.conditions.append(refined)
            les.refinements.append(f"narrowed to {refined.key} {refined.op} {refined.value} after {episode.id}")
            self.graph.save_lesson(les)
            return
        les.contradictions += 1
        self.graph.save_lesson(les)
        if les.contradictions >= CONTRADICTION_LIMIT:
            st = self.graph.states([les.id])[les.id]
            st.strength *= 0.5
            self.graph.set_state(les.id, team_id, st)
            self.graph.set_link(team_id, episode.id, les.id, "contradicts", 1.0)

    def _distinguishing_condition(self, team_id: str, les: Lesson, episode: Episode) -> Condition | None:
        """A condition every supporting episode shares and the contradicting one doesn't, e.g. rps <= 5000."""
        support = [e for e in (self.store.get(i, team_id) for i in les.evidence) if e is not None]
        if not support:
            return None
        for c in episode.conditions:
            if c.op != "=" or any(x.key == c.key for x in les.conditions):
                continue
            values = [next((x.value for x in e.conditions if x.key == c.key and x.op == "="), None) for e in support]
            if any(v is None for v in values):
                continue
            try:
                nums, mine = [float(v) for v in values], float(c.value)
            except (TypeError, ValueError):
                if len({str(v).lower() for v in values}) == 1 and str(values[0]).lower() != str(c.value).lower():
                    return Condition(key=c.key, op="=", value=values[0])
                continue
            if mine > max(nums):
                return Condition(key=c.key, op="<=", value=max(nums))
            if mine < min(nums):
                return Condition(key=c.key, op=">=", value=min(nums))
        return None

    # --- consolidation (§7) ----------------------------------------------------------------

    def _maybe_consolidate(self, team_id: str) -> None:
        every = self.settings.consolidation_every
        if not every:
            return
        total = self.store.count(team_id) + self.graph.archived_count(team_id)
        if total - self.graph.last_consolidation_seen(team_id) >= every:
            try:
                self.consolidate(team_id)
            except Exception as exc:  # the sleep job must never break a write
                log.warning("consolidation failed: %s", exc)

    def consolidate(self, team_id: str) -> dict:
        from horizon.memrouter.consolidate import consolidate

        return consolidate(self, team_id)

    # --- simulation reuse (MEMROUTER.md §12 lookupSimulation, §13) -----------

    def record_spike(self, *, team_id: str, situation: str, option: str, result: dict,
                     project_id: str | None = None) -> str:
        return self.spikes.add(team_id, project_id, situation, option, result, self.embedder.model_name,
                               self._embed(spike_text(situation, option)))

    def lookup_simulation(self, *, team_id: str, situation: str, options: list[str]) -> dict[str, dict]:
        """Past spike results for these options in a similar decision: {option: past}. Options without a
        close enough match are left out."""
        found = {}
        for option in options:
            hits = self.spikes.search(team_id, self._embed(spike_text(situation, option)),
                                      self.embedder.model_name)
            for past, sim in hits:
                if sim >= self.settings.spike_reuse_similarity and same_option(past["option"], option):
                    found[option] = {**past, "similarity": round(sim, 3)}
                    break
        return found

    # --- read path (MEMROUTER.md §6) ---------------------------------------------------------

    def recall(
        self,
        *,
        team_id: str,
        situation: str,
        conditions: list[Condition] | None = None,
        token_budget: int | None = None,
        task_id: str | None = None,
        project_id: str | None = None,
    ) -> ContextSlice:
        conditions = conditions or []
        budget = self.settings.recall_token_budget if token_budget is None else token_budget
        vector = self._embed(episode_text(situation, conditions))
        model, k, floor = self.embedder.model_name, self.settings.recall_top_k, self.settings.recall_min_similarity

        # Steps 1-2: candidates (episodes, lessons, strategies) by similarity x conditionMatch x scope x strength.
        nodes: dict[str, Node] = {}
        for ep, sim in self.store.search(team_id, vector, model, k):
            if sim >= floor:
                nodes[ep.id] = Node(ep.id, "episode", ep, sim)
        for les, sim in self.graph.search_lessons(team_id, vector, model, k):
            if sim >= floor or (les.is_fear and sim >= FEAR_SIMILARITY):
                nodes[les.id] = Node(les.id, "fear" if les.is_fear and not les.cleared_by else les.kind, les, sim)

        def fit(n: Node) -> float:
            scope = 1.0 if project_id is None or n.project_id in (None, project_id) else OTHER_SCOPE
            return condition_match(conditions, n.conditions) * scope

        states = self.graph.states(list(nodes))
        activation = {i: n.similarity * fit(n) * (0.2 + 0.8 * states[i].strength) for i, n in nodes.items()}

        # Step 3: spreading activation, 1-2 hops, decayed per hop and scaled by link weight.
        frontier = dict(activation)
        for _ in range(self.settings.activation_hops):
            spread: dict[str, float] = {}
            for src, dst, _kind, weight in self.graph.neighbours(list(frontier)):
                if weight > 0 and frontier[src] > 0:
                    spread[dst] = spread.get(dst, 0.0) + frontier[src] * weight * self.settings.activation_decay
            new = [i for i in spread if i not in nodes]
            nodes.update(self._nodes(team_id, new))  # memories reached only through links (archived ones drop out)
            spread = {i: a * fit(nodes[i]) for i, a in spread.items() if i in nodes}
            for i, a in spread.items():
                activation[i] = activation.get(i, 0.0) + a
            frontier = spread
        states.update(self.graph.states([i for i in nodes if i not in states]))

        # Step 4: shortlist. Fear lessons whose conditions don't rule them out always surface (§9).
        fears = [i for i, n in nodes.items() if n.kind == "fear" and n.similarity >= FEAR_SIMILARITY
                 and condition_match(conditions, n.conditions) > CONDITION_MISMATCH]
        ranked = sorted((i for i in nodes if i not in fears and activation.get(i, 0.0) > 0),
                        key=lambda i: -activation[i])
        shortlist = ranked[: self.settings.shortlist_size]

        # Step 5: the attention filter picks what enters working memory.
        chosen, filtered_by = self._attend(situation, conditions, [nodes[i] for i in shortlist])
        items, used, truncated = [], 0, False
        for i in fears + chosen:
            item = self._item(nodes[i], states[i], activation.get(i, 0.0))
            cost = estimate_tokens(json.dumps(item.model_dump(mode="json")))
            if i not in fears and (len(items) >= self.settings.recall_max_items or used + cost > budget):
                truncated = True
                break
            items.append(item)
            used += cost
        truncated = truncated or len(items) < len(fears) + len(chosen)

        recall_id = new_id("rc")
        self.store.log_recall(recall_id, team_id, task_id, situation, [m.episode_id for m in items])
        return ContextSlice(recall_id=recall_id, memories=items, candidates=len(nodes), truncated=truncated,
                            filtered_by=filtered_by, fear_warnings=len(fears))

    def _attend(self, situation: str, conditions: list[Condition], shortlist: list[Node]) -> tuple[list[str], str]:
        """§6 step 5: Jev keeps the memories it judges relevant, most relevant first. Without Jev (or if it
        fails) the activation order stands."""
        scorer = None
        if self._attention is not None and shortlist:
            try:
                scorer = self._attention()
            except Exception as exc:
                log.warning("attention filter unavailable: %s", exc)
        if scorer is None:
            return [n.id for n in shortlist], "activation"
        from horizon.decision.scorers import noul

        state = {"situation": situation, "conditions": [c.model_dump(mode="json") for c in conditions],
                 "memories": [{"kind": n.kind, "situation": n.obj.situation, "option": n.option,
                               "outcome": n.obj.statement if isinstance(n.obj, Lesson) else describe_outcome(n.obj)}
                              for n in shortlist]}
        questions = {f"m{i}": noul(f"Would `memories[{i}]` help decide `situation` well (relevant, and its "
                                   f"conditions fit)?") for i in range(len(shortlist))}
        try:
            answers = scorer.ask(state, questions)
        except Exception as exc:
            log.warning("attention filter failed: %s", exc)
            return [n.id for n in shortlist], "activation"
        keep = sorted((i for i in range(len(shortlist)) if answers[f"m{i}"].value >= self.settings.attention_threshold),
                      key=lambda i: -answers[f"m{i}"].value)
        return [shortlist[i].id for i in keep], scorer.name

    def _item(self, n: Node, st: State, activation: float) -> MemoryItem:
        if isinstance(n.obj, Episode):
            ep = n.obj
            return MemoryItem(episode_id=ep.id, kind="episode", situation=ep.situation, chosen=ep.chosen.label,
                              outcome=describe_outcome(ep), success=round(ep.actual.success, 3),
                              surprise=round(ep.surprise, 3), similarity=round(n.similarity, 3),
                              conditions=ep.conditions, severity=ep.severity, recorded_at=ep.provenance.created_at,
                              strength=round(st.strength, 3), activation=round(activation, 3))
        les = n.obj
        tr = les.track_record
        total = tr.successes + tr.failures
        return MemoryItem(episode_id=les.id, kind=n.kind, situation=les.situation, chosen=les.option,
                          outcome=les.statement, success=round(tr.successes / total, 3) if total else 0.0,
                          surprise=0.0, similarity=round(n.similarity, 3), conditions=les.conditions,
                          severity="severe" if n.kind == "fear" else "normal", recorded_at=les.updated_at,
                          strength=round(st.strength, 3), evidence=len(les.evidence), activation=round(activation, 3))
