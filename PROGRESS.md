# Progress

Updated at the end of every session. See `CLAUDE.md` for the working rules and `docs/PROJECT.md` §13 for the build order.

## Completed

- **Session 0 — repo setup (no platform code).**
  - Moved `PROJECT.md` and `MEMROUTER.md` into `docs/`.
  - Removed the empty root file `Docs`, which held only a newline. It would clash with the `docs/` folder on case-insensitive filesystems (macOS, Windows).
  - Added `CLAUDE.md` (session rules) and this `PROGRESS.md`. Merged in PR #1.
  - Applied two rounds of owner decisions to both docs (see "Decisions made"). PR #1 was merged before round 1 was pushed, so rounds 1 and 2 go in a follow-up PR.
  - Wrote the Phase 1 spec as `docs/PROJECT.md` §18, including the domains and credentials real runs need.

## In progress

- Nothing. No build phase has started.

## Decisions made

**Repo conventions**
- Project docs live in `docs/`. The two docs refer to each other by bare filename, and that still works because they sit in the same folder.
- `docs/PROJECT.md` wins over `docs/MEMROUTER.md` wherever they conflict.
- Unclear or contradictory spec points go in "Open questions" below. They are not resolved by guessing during a build session.

**Owner decisions, round 1**
1. **`ARCHITECTURE.md` is retired.** `PROJECT.md` replaces it. All references to it were removed from both docs.
2. **MCP-first is confirmed.** The MCP server is the product. `MEMROUTER.md` §12 now maps its interfaces to MCP tools, and the Python SDK moves to "Later".
3. **Basic memory moves to Phase 2.** Memrouter build step 1 (episodes, write path, basic similarity recall) now ships in Phase 2 with the MCP skeleton. Phase 6 adds the learning features: surprise-based links, spreading activation, decay, consolidation and fear memories.
   - In the docs, Phase 6 also covers MEMROUTER steps 3 (Jev attention filter) and 7 (predictor trust, simulation reuse). See open question 3.
4. **Surprise is a signed prediction error** (`MEMROUTER.md` §5):
   - `outcome_score = 0.6 × success + 0.4 × efficiency`
   - `success` = test pass rate (0 to 1)
   - `efficiency` = average of `min(1, predicted/actual)` over tokens, cost and latency
   - `predicted_score = 0.6 × predicted_success_probability + 0.4`
   - `surprise = outcome_score − predicted_score` (−1 to 1)
   - The sign sets the direction of the link update and the magnitude sets its size. The weights are configurable.
5. **Episodes are never deleted.** The sleep job archives low-value episodes to cold storage, which is excluded from retrieval but kept as world-model training data. Pruning applies only to links and the retrieval index.
6. **Tech stack** (`PROJECT.md` §17):
   - Python 3.12 with the official MCP Python SDK.
   - Streamable HTTP transport for the hosted server (API key auth); stdio for local development.
   - FastAPI for the API key, credits and usage endpoints.
   - Postgres + pgvector, pytest, and Docker, with everything containerised. Hosting is decided at launch.

**Owner decisions, round 2**
7. **Surprise edge cases** (`MEMROUTER.md` §5):
   - **Actual value is 0:** the ratio is 1, computed as `min(1, predicted / max(actual, ε))`.
   - **Predicted value missing:** drop that metric from the efficiency average. If all three are missing, score on success only (weights renormalised).
   - **No success probability:** use the historical success rate of similar episodes, or 0.5 if there are none. Mark the episode `lowConfidence` and halve its learning rate.
8. **Cold storage:** a separate Postgres table, `episodes_archive`, in the same database, plus a periodic Parquet export for world-model training. Move to object storage only when size requires it.
9. **Embeddings:** `BAAI/bge-small-en-v1.5` via fastembed, behind the embedding interface so it can be swapped.
10. **Phase 1 baseline:**
    - mini-SWE-agent with `claude-sonnet-5` via the Anthropic API.
    - Claude Code headless is added later as a second baseline.
    - The baseline and all later platform runs must use the same agent and model.
11. **Phase 1 tasks:**
    - SWE-bench Verified, not Lite.
    - 50 tasks chosen with a fixed seed, stratified across repos, and weighted toward the longer difficulty buckets.
    - The task list is committed to the repo.
12. **Phase 1 budget and execution:**
    - Caps of 50 steps and $1 per task.
    - A 10-task smoke run, then the full 50, then the full baseline 3 times to measure variance.
    - Agent runs happen in the cloud session, with evaluation via sb-cli rather than local Docker.
    - Record success, tokens, cost, steps and time per task.
13. **No API credits yet:**
    - Build the full harness with a mock-model dry-run mode, but make no real runs and no paid API calls.
    - Real-run settings stay in config behind one flag.
    - The domains and credentials for real runs are listed in `PROJECT.md` §18.
    - Proceed to Phase 2 once the harness PR is merged.

**My interpretations while editing the docs (please confirm)**
- **`MEMROUTER.md` §12 tool mapping:**
  - `recall` → `recall_context`; `record` → `record_outcome`.
  - `lookupSimulation` is used inside `evaluate_options` / `submit_consequences`.
  - `predictorTrust` and `consolidate` are internal.
  - `clearFear` → `clear_fear`, human only.
- **Data model:**
  - `predicted.success` is the predicted success probability.
  - `actual.success` is the test pass rate.
  - `Episode` gains `surprise`, `lowConfidence` and `archivedAt` fields.
- **Defaults I picked** (`MEMROUTER.md` §14):
  - ε = 1e-9.
  - The Parquet export runs with each consolidation run.
- **"Similar episodes"** for the fallback success probability means the Phase 2 basic similarity recall (§6 step 2), over episodes that aren't archived.

## Open questions

Items marked **(blocks Phase 1)** need an answer or a decision during the Phase 1 build.

### Background

1. **Existing memrouter project.** `PROJECT.md` §15 says to "reconcile with the existing memrouter project (results not yet shared)". Where is that project? Should it shape Phase 2 and Phase 6?
2. **Name clash.** Another GitHub project is already called MemRouter (§15). Should the component keep the name `memrouter` for now?

### Build-order details

3. **Memory in Phases 4 and 5.** Proposal: Phases 4 and 5 use the Phase 2 basic similarity recall for "memory feeds Jev evidence" and "memory lookup". Full simulation reuse, predictor trust and the Jev attention filter arrive in Phase 6. OK?
4. **Task state and memory storage.** Task state must keep working if memrouter is down (MEMROUTER §11). Should it be a separate service or database, or the same Postgres with separate tables and a failure-isolated code path?
5. **When does API key auth start?** Key, credits and usage endpoints are Phase 7. Should Phases 2–6 use stdio locally with no auth, plus a single static dev key for HTTP?

### Phase 1: benchmark harness

6. **(blocks Phase 1) Where does the agent execute task code?** mini-SWE-agent's SWE-bench mode normally runs each task inside that task's SWE-bench Docker image. The cloud session may not have Docker. The options are:
   - (a) Docker in the session, if available (pulls images from Docker Hub, so that domain would be needed);
   - (b) a local environment per task (clone the repo and install its dependencies; slower and can differ from the evaluation environment);
   - (c) a remote sandbox provider (a new service and credential).

   sb-cli only covers evaluation, not the agent's own runs. Proposal: support (a) with (b) as a fallback, and check which one works when real runs are enabled. This doesn't block the dry-run build.
7. **Difficulty weighting.** SWE-bench Verified has four difficulty buckets (<15 min, 15 min–1 h, 1–4 h, >4 h), and the longest has very few tasks. Proposal:
   - take every >4 h task;
   - then roughly 40% from 1–4 h, 35% from 15 min–1 h and 25% from <15 min;
   - stratify by repo within each bucket.

   Are those weights OK?
8. **Smoke run selection.** Should the 10 smoke tasks be the first 10 of the committed 50 (keeping the same stratification), or a separate set?
9. **Model pricing.** mini-SWE-agent tracks cost through litellm. If litellm doesn't yet list `claude-sonnet-5` prices, the $1 cap can't be enforced. Proposal: set per-token prices in harness config and use them for both the cap and the reports.
10. **"Benchmark after every phase".** Phases 2–3 don't change agent behaviour much. Is a no-regression check enough for those phases?

### Decision layer and Jev

11. **Jev access is unverified** (§15). Should the small-LLM comparison scorer (§5) be built first as a fallback behind the same interface?
12. **Thresholds undefined.** There are no values yet for the "clear winner" and "close call" thresholds, the score weights, or crucial-decision detection.
13. **"Ask human if high stakes."** A concrete rule is needed beyond the examples given.

### Memrouter spec gaps

14. **Surprise when predicted and actual are both 0.** With `max(actual, ε)`, the ratio comes out as 0 instead of 1 (for example, a predicted cost of $0 when the actual cost is $0). Should this case count as a ratio of 1?
15. **Decay maths is undefined.** No formulas are given for how `strength` and `stability` change per recall or per "usage opportunity", and "helpful" isn't defined.
16. **Undefined terms.** "Condition match" scoring for mixed operators is not defined. Neither is who classifies a failure as "severe".

### Outcomes, privacy and data

17. **Undefined specs.** "Heavy testing" and the world-model logging format are both undefined. The logging format is needed from Phase 1/2 ("from day one"). Proposal: Phase 1 writes one JSON Lines record per task run with a versioned schema, and the Phase 2 episode format builds on it.
18. **Raw code could be stored.** Test output and `testResults` can contain code, stack traces and file paths. What sanitisation or redaction is required?

### Host integration

19. **Which hosts first?** Should Phase 2 target Claude Code only, or also Codex and Cursor? What target invocation rate counts as "reliable"?

## Next step

Merge the follow-up docs PR. Then start **Phase 1 — Benchmark harness + baseline** as specified in `docs/PROJECT.md` §18:
- mini-SWE-agent with `claude-sonnet-5` on 50 SWE-bench Verified tasks;
- a dry-run mode with a mock model, and real runs behind one config flag;
- no paid API calls.

Questions 6–9 can take the proposed defaults during the build unless answered first.
