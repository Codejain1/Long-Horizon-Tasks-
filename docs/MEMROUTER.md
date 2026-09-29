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
  predicted: { success, tokens, costUsd, latencyMs, confidence, source: "jev" | "sim" | "memory" | "host" | "llm" },
                             // predicted.success = probability of success, 0..1
                             // source "host" = the host LLM's own estimate (the only source before Jev and simulation exist)
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
  source: "jev" | "sim" | "memory" | "host" | "llm", taskType,   // llm = the small-LLM comparison scorer (PROJECT.md §5)
  calibration, accuracy, samples
}
```

**As built:**
- Lessons and strategies share one `lessons` table. `kind` is lesson or strategy; the fields are `statement`, `situation`, `option`, `conditions`, `evidence`, `track_record`, `is_fear`, `cleared_by`, `contradictions` and `refinements`.
- **`strength` and `stability` live in `memory_state` for every memory, episodes included**, so one learning rule ranks all kinds. They start at 0.5 and 1.
- `PredictorStats` also scores the world model (`PROJECT.md` §6) as source `world_model`, from the forecast it logged before the outcome.
- `PredictorStats` is kept as sums per (team, source, task type). `calibration` = mean predicted − mean actual (0 = calibrated, + = overconfident), `accuracy` = 1 − mean absolute error, plus the Brier score. `taskType` is a `task_type` condition, else "general".
- Other tables: `links`, `weight_log` (every weight change, §11), `spike_results` (§12 `lookupSimulation`), `recall_log` (with the tokens routed), `memory_removals`, `episodes_archive`, `consolidation_runs`.

Storage: Postgres + pgvector to start (nodes, links, weights, embeddings). Move to a graph database only if link traversal becomes a bottleneck. Archived episodes live in **cold storage**: kept permanently as world-model training data, excluded from retrieval.

Cold storage is a separate Postgres table, `episodes_archive`, in the same database for now. A periodic job exports episodes to **Parquet** files for world-model training. Move the archive to object storage only when size requires it.

Embeddings sit behind an embedding interface so the model can be swapped. The default is **`BAAI/bge-small-en-v1.5` via fastembed**, which is small, CPU-only and needs no PyTorch.

## 5. Write path (after every outcome)

1. **Record episode** with predicted vs actual outcome, conditions, provenance.
2. **Compute surprise** as a signed, zero-centred prediction error:
   ```
   success           = actual test pass rate (see PROJECT.md §9 for the baseline rule)   (0..1)
   success_error     = success − predicted_success_probability                        (−1..1)
   efficiency_error  = for each of tokens, cost, latency with a valid prediction:
                       clamp((predicted − actual) / predicted, −1, 1)                 (+ = cheaper than predicted)
   surprise          = 0.6 × success_error + 0.4 × mean(efficiency_errors)             (−1..1)
   ```
   - An accurate predictor gets surprise ≈ 0 on average. Better-than-predicted and worse-than-predicted outcomes are both possible for success and for every efficiency metric.
   - The weights (0.6 / 0.4) are configurable defaults (§14).

   **Edge cases:**
   - **Predicted value missing or 0:** drop that metric (no valid ratio). If none remain: `surprise = success_error`.
   - **Actual value is 0:** the metric's error is +1 (as cheap as possible).
   - **No success probability:** use memrouter's historical success rate for similar episodes (basic similarity recall, §6 step 2). If there are none, use 0.5. Mark the episode `lowConfidence` and halve its learning rate in step 3.
3. **Update links** among memories that were recalled for this decision:
   `Δweight = learningRate × surprise × signalWeight` (× 0.5 if the episode is `lowConfidence`)
   - The **sign** of `surprise` sets the direction (better than predicted → strengthen, worse → weaken); its **magnitude** sets the size.
   - `signalWeight`: human > auto > implicit (e.g. 1.0 / 0.7 / 0.4).
   - Expected outcomes barely change weights; surprising ones change them a lot.
   **As built (credit assignment and decay, §8):**
   - Only recalled memories about **the option the host implemented** "fed" the decision.
   - Their strength moves by `Δ = learningRate × signalWeight × surprise`. A loss is divided by √stability, so proven memories lose less.
   - A **helpful** recall (surprise ≥ 0 and success ≥ 0.5) multiplies stability by 1.5, capped at 20.
   - Co-success links among the fed memories and the new episode start at 0.1 and move by the same Δ.
   - Recalled memories about other options had a usage opportunity and didn't help: strength − 0.02 / stability.
   - New episodes get semantic links (0.1) to up to 3 memories with similarity ≥ 0.6.
4. **Update predictor stats** for Jev / simulation / memory / host (were their predictions right?).
   - Each source's **raw** prediction is scored.
   - When a missing prediction was filled from similar episodes, memory is the source scored.
   - Once a scorer has 20+ outcomes, its measured bias is subtracted from its success estimates before ranking (`PROJECT.md` §5). The raw value is still what gets recorded.
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

**As built:**
- **Candidates:** the top 50 episodes and the top 50 lessons/strategies with similarity ≥ 0.2.
- **Seed activation** = similarity × conditionMatch × scope × (0.2 + 0.8 × strength). The strength factor is bounded, so learning reorders memories but never erases a highly similar one.
- **conditionMatch:** current conditions are facts and stored ones may be predicates. A key is compared when one side is `=`. The result is 1.0 when every comparable key matches, 0.25 when none do, and 0.75 when nothing is comparable.
- **Scope (§10):** the same task 1.0, the same project 0.95, project-less memories 1.0, another project `other_project_factor` (0.85). Setting that factor to 0 gives hard project walls.
- **Spreading:** 2 hops, contribution = activation × link weight × 0.5 per hop. It follows semantic, co-success and derived-from links in both directions, never `contradicts`, and scope and conditions apply to reached memories too.
- **Shortlist:** 25 by activation.
- **Attention filter:** one Jev Noul per shortlisted memory ("would it help decide `situation` well?"), kept at ≥ 0.5 and ordered by it. Without Jev, the activation order is used.
- **Fear lessons** with similarity ≥ 0.5, not ruled out by conditions, always come first, even past the budget.
- **Past spike results** (similarity ≥ 0.5, at most 2) follow within the budget.

## 7. Consolidation ("sleep" job)

Runs periodically offline (e.g. nightly or after N episodes):
- **Replay** recent episodes, grouped by situation and conditions.
- **Extract** new lessons and strategies where episodes agree; attach evidence links.
- **Merge** duplicates; strengthen lessons confirmed by new episodes.
- **Archive** low-value episodes whose lessons are already consolidated: move them to cold storage, `episodes_archive` (excluded from retrieval, kept as world-model training data; evidence references stay valid). **Episodes are never deleted.**
- **Prune** weak links and stale entries in the retrieval index. Pruning applies only to links and the index, never to episodes.
- **Refresh** predictor calibration stats.

This keeps retrieval lean, turns experience into reusable knowledge, and preserves the full episode history for training.

**As built** (`horizon consolidate`, or inline every 200 episodes; the Docker `sleep` service runs it nightly):
- **Replay and extract:** the last 1,000 episodes are clustered by the same option and similarity ≥ 0.8. 3+ episodes at ≥ 80 % success become a strategy; at ≤ 30 %, a failure lesson. Mixed clusters are left to reconsolidation. Conditions are those shared by every episode in the cluster.
- **Merge:** into an existing lesson of the same kind and option at similarity ≥ 0.85 (strength + 0.1, stability × 1.2).
- **Archive:** everything but the newest 3 evidence episodes per lesson.
- **Prune:** links below 0.05.
- **Export:** a Parquet file of all episodes, archived ones included (the `[export]` extra).

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

**As built:**
- **What counts as a contradiction:** a strategy about the implemented option followed by success < 0.5, or a failure lesson followed by success ≥ 0.5.
- **Refinement:** look for a condition that every supporting episode shares and the contradicting one doesn't. Numbers become `<=` the maximum or `>=` the minimum; categories become `=` the shared value.
- **Repeats:** with no such condition, contradictions are counted, and from the 2nd the lesson's strength halves and it gets a `contradicts` link.
- **Confirmations** extend the track record and the evidence.

## 9. Fear memories

- A **severe** failure (data loss, security issue, destructive action, large unplanned spend) instantly creates a strong negative lesson (`isFear: true`).
- Fear lessons are always surfaced when conditions match, and Jev must see them before scoring.
- Guard against flukes: fear lessons can be weakened by later contrary evidence or cleared by a human, never silently.

**As built:**
- **Creation:** a `severity: "severe"` outcome creates a fear lesson at strength 1.0 and maximum stability. `PROJECT.md` §9 also escalates the task to a human at once.
- **Contrary evidence:** a later success of ≥ 0.8 with the same option, under conditions that don't rule the fear out, multiplies strength by 0.7 and records why.
- **Clearing:** only through the MCP tool `clear_fear` (the user confirms and gives a name, via an MCP user-input request) or the operator CLI `horizon clear-fear --by <name>`.

## 10. Scope, privacy and the colony layer

- **Scopes:** task → project → team. Retrieval prefers the narrowest matching scope.
- **Isolation:** each team's memory is fully isolated. No customer code or details ever leave its scope.
- **Colony layer (later, opt-in):** share only anonymous "trail strengths" for generic lessons, e.g. "lib A v2 conflicts with lib B v5". Trails strengthen when many teams confirm them and evaporate when unused, like ant pheromones.

## 11. Robustness

- **Not a single point of failure:** task state and checkpoints live outside memrouter. If memrouter is down or slow, agents continue on task state alone.
- **Provenance** on every memory, so bad memories can be traced and removed before spreading.
- **Conflicts:** when agents write contradicting memories, keep both with evidence; outcomes decide which strengthens.
- **Auditability:** every write, recall and weight change is logged.

**As built:**
- **Weight changes:** `weight_log` records each strength, stability and link change with its reason (for example "fed ep_… surprise +0.40"). `show_memories` shows a memory's last changes.
- **Removal:** `delete_memory` archives episodes and deletes lessons, keeping a snapshot.
- **Erasure:** operator-only `horizon purge-memory` hard-erases one episode and its references for legal requests. It's the only exception to "episodes are never deleted", and past exports it touched are listed for rebuilding.

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
| `predictorTrust` | internal: it corrects a scorer's measured bias before ranking; reported in `horizon stats` |
| `consolidate` | internal scheduled job (`horizon consolidate`), not host-callable |
| `flagFear` / `clearFear` | automatic on severe outcomes / `clear_fear` (human only, confirmed through MCP user input) |

Inspection is served by `show_memories` (with each memory's provenance and weight history) and `delete_memory` (`PROJECT.md` §11).

**Later:** a Python SDK exposing the same interfaces, alongside the LangGraph adapter (`PROJECT.md` §13, item 9).

## 13. Role in the pipeline

- **Before decisions:** supplies past strategies as candidates and their track records as Jev evidence.
- **Instead of simulations:** returns past spike results when a similar tie was already tested.
- **During execution:** routes only relevant, proven context to the working LLM.
- **After testing:** learns from outcomes and predictor accuracy.
- **For the world model:** episodes, archived ones included, are the training data. The first learned model (`kernel-v1`, `PROJECT.md` §6) reads them directly. A Dreamer-style latent model can replace it behind the same interface once it beats the stand-ins.

## 14. Default parameters (starting points, tune with data)

| Parameter | Default |
|---|---|
| Candidate pull (top-k) | 50 |
| Activation hops | 2, decay 0.5 per hop |
| Shortlist before Jev | 20–30 |
| New semantic link weight | 0.1 |
| Learning rate | 0.2 |
| Surprise weights (success / efficiency) | 0.6 / 0.4 |
| Fallback success probability (no history) | 0.5 |
| Learning-rate multiplier for low-confidence episodes | 0.5 |
| Embedding model | `BAAI/bge-small-en-v1.5` (fastembed, 384 dims) |
| Parquet export cadence | with each consolidation run |
| Signal weights (human / auto / implicit) | 1.0 / 0.7 / 0.4 |
| Link prune threshold | 0.05 |
| Consolidation cadence | nightly or every 200 episodes |
| Semantic links per new episode (min. similarity) | 3 (0.6) |
| Default strength / stability; stability growth; maximum | 0.5 / 1; × 1.5 per helpful recall; 20 |
| Usage-opportunity decay | 0.02 / stability |
| Scope factors (task / project / other project) | 1.0 / 0.95 / 0.85 (0 = walls) |
| Attention filter threshold | Jev Noul ≥ 0.5 |
| Lesson extraction (min. episodes; strategy ≥; lesson ≤) | 3; 80 %; 30 % |
| Evidence kept in retrieval per lesson | 3 newest |
| Scorer bias correction | after 20 outcomes |
| World model: kernel width; minimum similarity; prior weight | 0.1; 0.5; 1 episode |
| World model gate: paired outcomes per stand-in; test | 30; one-sided paired, 95 % |
| World model: confidence to settle a close call | 0.6 (≈ 4.5 effective episodes) |

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

**Status:** steps 1–7 are built and tested. A simulated repeated-task test shows retrieval precision rising from 0.00 to 1.00 by round 4 with learning, and staying at 0 without it (`tests/test_learning.py`). That's one constructed scenario; the real evidence will come from repeated benchmark runs (`horizon-bench run --agent claude-code --with-horizon`, `PROJECT.md` §18). Step 8 is later.
