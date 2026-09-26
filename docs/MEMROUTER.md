# Memrouter — Specification

> **Status: DESIGN.** Companion to `PROJECT.md`, which is the source of truth and wins on any conflict. Memrouter is the shared, outcome-learning memory layer of the long-horizon agent system.
>
> **One line:** memory that learns like a brain, engineered like a database.

---

## 1. Purpose

Other memory systems remember **what happened**. Memrouter remembers **what worked**, under which conditions, and routes only that into each agent's context.

It must:
- Give every agent (main agent, subagents, later tasks, new sessions) a small, relevant, proven context slice.
- Learn from real outcomes without manual curation.
- Supply evidence to Jev and reuse past simulation results to cut cost.
- Stay safe: isolated per team, traceable, and never a single point of failure.

## 2. Design choices (final)

Structure, scope and safety rails come from engineering. Learning rules come from biology.

| Question | Chosen answer | Inspiration |
|---|---|---|
| Memory unit | Decision records stored as episodes, consolidated into lessons and strategies by a periodic "sleep" job | Hippocampus → neocortex consolidation |
| Link creation | Links strengthen by **surprise** (prediction error), not raw success | Hebbian learning + dopamine reward prediction error |
| Routing | Cheap candidate pull via similarity + **spreading activation**, then Jev as **attention filter** | Spreading activation + prefrontal attention |
| Decay | Usage-based decay with **spaced repetition** (successful recall slows future decay); pruning of weak links | Forgetting curve, synaptic pruning |
| Context-dependence | Explicit **conditions** on every outcome + **reconsolidation** (contradiction updates conditions instead of just weakening) | Context-dependent memory, reconsolidation |
| Scope | Per-team isolation first; later opt-in **colony layer** sharing only anonymous trail strengths | Ant pheromone trails (stigmergy) |
| Severe failures | **Fear memories**: instant strong negative memory from one serious failure, but still revisable | Fear conditioning (amygdala) |

## 3. Memory types

1. **Episodes** — raw decision records (what happened). High volume, short-to-medium lifespan.
2. **Lessons** — extracted facts with conditions, e.g. "library X fails above 10k rps on stack Y" (what is true).
3. **Strategies** — reusable approaches with track records, e.g. "for auth in Next.js apps, use approach Z" (what works).

Episodes are the evidence. Lessons and strategies are distilled from episodes during consolidation and always keep links back to their source episodes.

## 4. Data model

```ts
Episode {
  id, scope: { teamId, projectId?, taskId? },
  situation,                 // compact description of the decision point
  conditions: Condition[],   // stack, scale, project size, domain...
  chosen: OptionRef,
  alternatives: OptionRef[],
  predicted: { success, tokens, costUsd, latencyMs, confidence, source: "jev" | "sim" | "memory" },
                             // predicted.success = probability of success, 0..1
  actual:    { success, tokens, costUsd, latencyMs, testResults, signalType: "auto" | "implicit" | "human" },
                             // actual.success = test pass rate, 0..1
  surprise,                  // signed, -1..1 (see §5)
  lowConfidence: boolean,    // true when no success probability was predicted (§5)
  severity: "normal" | "severe",
  provenance: { agentId, taskId, createdAt },
  archivedAt?,               // set when moved to cold storage by the sleep job (§7)
  embedding
}

Lesson {
  id, scope, statement, conditions: Condition[],
  evidence: EpisodeId[], strength, stability, lastRecalledAt,
  isFear: boolean, embedding
}

Strategy {
  id, scope, description, conditions: Condition[],
  trackRecord: { successes, failures, humanWeighted },
  evidence: EpisodeId[], strength, stability, lastRecalledAt, embedding
}

Link {
  fromId, toId,
  weight,          // 0..1, learned
  kind: "co-success" | "semantic" | "derived-from" | "contradicts",
  lastUpdatedAt
}

Condition { key, op, value }   // e.g. { key: "rps", op: ">", value: 10000 }

PredictorStats {                // tracks whose forecasts can be trusted
  source: "jev" | "sim" | "memory", taskType,
  calibration, accuracy, samples
}
```

Storage: Postgres + pgvector to start (nodes, links, weights, embeddings). Move to a graph database only if link traversal becomes a bottleneck. Archived episodes live in **cold storage**: kept permanently as world-model training data, excluded from retrieval.

Cold storage is a separate Postgres table, `episodes_archive`, in the same database for now. A periodic job exports episodes to **Parquet** files for world-model training. Move the archive to object storage only when size requires it.

Embeddings sit behind an embedding interface so the model can be swapped. The default is **`BAAI/bge-small-en-v1.5` via fastembed**, which is small, CPU-only and needs no PyTorch.

## 5. Write path (after every outcome)

1. **Record episode** with predicted vs actual outcome, conditions, provenance.
2. **Compute surprise** as a signed prediction error:
   ```
   success         = actual test pass rate                       (0..1)
   efficiency      = mean over {tokens, cost, latency} of
                     min(1, predicted / actual)                  (0..1)
   outcome_score   = 0.6 × success + 0.4 × efficiency
   predicted_score = 0.6 × predicted_success_probability + 0.4
   surprise        = outcome_score − predicted_score             (−1..1)
   ```
   - `predicted_score` assumes the prediction expects to land on budget (efficiency = 1).
   - The weights (0.6 / 0.4) are configurable defaults (§14).

   **Edge cases:**
   - **Actual value is 0:** that metric's ratio is 1. Computed as `min(1, predicted / max(actual, ε))`.
   - **Predicted value missing:** drop that metric from the efficiency average. If all three are missing, score on success only by renormalising the weights: `outcome_score = success`, `predicted_score = predicted_success_probability`.
   - **No success probability:** use memrouter's historical success rate for similar episodes (basic similarity recall, §6 step 2). If there are none, use 0.5. Mark the episode `lowConfidence` and halve its learning rate in step 3.
3. **Update links** among memories that were recalled for this decision:
   `Δweight = learningRate × surprise × signalWeight` (× 0.5 if the episode is `lowConfidence`)
   - The **sign** of `surprise` sets the direction (better than predicted → strengthen, worse → weaken); its **magnitude** sets the size.
   - `signalWeight`: human > auto > implicit (e.g. 1.0 / 0.7 / 0.4).
   - Expected outcomes barely change weights; surprising ones change them a lot.
4. **Update predictor stats** for Jev / simulation / memory (were their predictions right?).
5. **Severe failure?** Create a fear lesson immediately (see §9).
6. **Contradiction?** If an outcome contradicts a recalled lesson, trigger reconsolidation (see §8).

New semantic-similarity links start weak (e.g. 0.1) and must earn strength through outcomes.

## 6. Read path (routing context to an agent)

1. **Seed**: embed the current situation + task state; match conditions.
2. **Pull candidates** (cheap): top-k by `similarity × conditionMatch`.
3. **Spreading activation**: propagate activation 1–2 hops through links, decayed per hop and scaled by link weight. Surfaces relevant memories that aren't textually similar.
4. **Shortlist** ~20–30 memories by activation.
5. **Attention filter**: Jev decides which few enter context, within a token budget (like limited working memory).
6. **Return** a compact context slice: relevant lessons, proven strategies, past spike results, fear warnings — each with its evidence strength.

Every recall is logged, so it can be strengthened or weakened by the outcome that follows.

## 7. Consolidation ("sleep" job)

Runs periodically offline (e.g. nightly or after N episodes):
- **Replay** recent episodes, grouped by situation and conditions.
- **Extract** new lessons and strategies where episodes agree; attach evidence links.
- **Merge** duplicates; strengthen lessons confirmed by new episodes.
- **Archive** low-value episodes whose lessons are already consolidated: move them to cold storage, `episodes_archive` (excluded from retrieval, kept as world-model training data; evidence references stay valid). **Episodes are never deleted.**
- **Prune** weak links and stale entries in the retrieval index. Pruning applies only to links and the index, never to episodes.
- **Refresh** predictor calibration stats.

This keeps retrieval lean, turns experience into reusable knowledge, and preserves the full episode history for training.

## 8. Decay and reconsolidation

**Decay (spaced repetition):**
- Each memory has `strength` and `stability`.
- Strength decays over *usage opportunities*, not wall-clock time alone: a memory weakens when retrieved but unhelpful, or when relevant situations pass without it being useful.
- Each successful recall boosts strength **and** increases stability, so future decay is slower.
- Proven memories decay very slowly but are never permanent (tools and libraries change).
- Links below a threshold are pruned.

**Reconsolidation:**
- When a recalled lesson is contradicted, don't just weaken it. First check whether conditions differ.
- If they do, **refine conditions** (split "X is bad" into "X fails above 10k rps").
- If they don't and contradictions repeat, weaken it and add a `contradicts` link.

## 9. Fear memories

- A **severe** failure (data loss, security issue, destructive action, large unplanned spend) instantly creates a strong negative lesson (`isFear: true`).
- Fear lessons are always surfaced when conditions match, and Jev must see them before scoring.
- Guard against flukes: fear lessons can be weakened by later contrary evidence or cleared by a human, never silently.

## 10. Scope, privacy and the colony layer

- **Scopes:** task → project → team. Retrieval prefers the narrowest matching scope.
- **Isolation:** each team's memory is fully isolated. No customer code or details ever leave its scope.
- **Colony layer (later, opt-in):** share only anonymous "trail strengths" for generic lessons, e.g. "lib A v2 conflicts with lib B v5". Trails strengthen when many teams confirm them and evaporate when unused, like ant pheromones.

## 11. Robustness

- **Not a single point of failure:** task state and checkpoints live outside memrouter. If memrouter is down or slow, agents continue on task state alone.
- **Provenance** on every memory, so bad memories can be traced and removed before spreading.
- **Conflicts:** when agents write contradicting memories, keep both with evidence; outcomes decide which strengthens.
- **Auditability:** every write, recall and weight change is logged.

## 12. Interfaces

```ts
recall(situation, taskState, tokenBudget) -> ContextSlice
record(episode) -> void               // triggers write path
lookupSimulation(situation, options) -> PastSpikeResults | null
predictorTrust(source, taskType) -> PredictorStats
consolidate() -> ConsolidationReport  // sleep job
flagFear(episodeId) / clearFear(lessonId, byHuman)
```

**MCP first.** The MCP server is the product (`PROJECT.md` §3, §11), so these interfaces are exposed through the platform's MCP tools first:

| Interface | MCP tool |
|---|---|
| `recall` | `recall_context` |
| `record` | `record_outcome` |
| `lookupSimulation` | used inside `evaluate_options` / `submit_consequences` |
| `predictorTrust` | internal (feeds Jev scoring) |
| `consolidate` | internal scheduled job, not host-callable |
| `flagFear` / `clearFear` | internal on severe outcomes / `clear_fear` (human only) |

Inspection is served by `show_memories` and `delete_memory` (`PROJECT.md` §11).

**Later:** a Python SDK exposing the same interfaces, alongside the LangGraph adapter (`PROJECT.md` §13, item 9).

## 13. Role in the pipeline

- **Before decisions:** supplies past strategies as candidates and their track records as Jev evidence.
- **Instead of simulations:** returns past spike results when a similar tie was already tested.
- **During execution:** routes only relevant, proven context to the working LLM.
- **After testing:** learns from outcomes and predictor accuracy.
- **For the future world model:** episodes form the training data for a Dreamer-style latent world model.

## 14. Default parameters (starting points, tune with data)

| Parameter | Default |
|---|---|
| Candidate pull (top-k) | 50 |
| Activation hops | 2, decay 0.5 per hop |
| Shortlist before Jev | 20–30 |
| New semantic link weight | 0.1 |
| Learning rate | 0.2 |
| Surprise weights (success / efficiency) | 0.6 / 0.4 |
| Surprise ε (floor on actual values) | 1e-9 |
| Fallback success probability (no history) | 0.5 |
| Learning-rate multiplier for low-confidence episodes | 0.5 |
| Embedding model | `BAAI/bge-small-en-v1.5` (fastembed, 384 dims) |
| Parquet export cadence | with each consolidation run |
| Signal weights (human / auto / implicit) | 1.0 / 0.7 / 0.4 |
| Link prune threshold | 0.05 |
| Consolidation cadence | nightly or every 200 episodes |

## 15. Success metrics

- Success rate and tokens per task across **repeated benchmark runs** (should improve over runs).
- Comparison vs a standard memory layer (e.g. mem0) on the same tasks.
- Share of ties settled from memory instead of simulation (should rise).
- Context tokens per step (should fall).
- Predictor calibration per source.
- Rate of bad memories traced and removed.

## 16. Build order

Step 1 ships in `PROJECT.md` Phase 2 (with the MCP skeleton, because `recall_context` and `record_outcome` need it). Steps 2–7 ship in `PROJECT.md` Phase 6. Step 8 is later.

1. Episodes + write path + basic similarity recall.
2. Surprise-based link learning + spreading activation.
3. Jev attention filter + token budget.
4. Decay with spaced repetition + pruning.
5. Consolidation sleep job + conditions + reconsolidation.
6. Fear memories.
7. Predictor trust tracking and simulation reuse.
8. Colony layer (opt-in, much later).

Benchmark after each step; keep only what moves the metrics.
