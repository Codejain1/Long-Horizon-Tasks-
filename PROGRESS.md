# Progress

Updated at the end of every session. See `CLAUDE.md` for the working rules and `docs/PROJECT.md` §13 for the build order.

## Completed

- **Session 0 — repo setup (no platform code).**
  - Moved `PROJECT.md` and `MEMROUTER.md` into `docs/`.
  - Removed the empty root file `Docs`, which held only a newline. It would clash with the `docs/` folder on case-insensitive filesystems (macOS, Windows).
  - Added `CLAUDE.md` (session rules) and this `PROGRESS.md`.
  - Reviewed both docs and logged the unclear and contradictory points.
  - Applied the owner's decisions on the first round of open questions to both docs (see "Decisions made").

## In progress

- Nothing. No build phase has started.

## Decisions made

**Repo conventions**
- Project docs live in `docs/`. The two docs refer to each other by bare filename, and that still works because they sit in the same folder.
- `docs/PROJECT.md` wins over `docs/MEMROUTER.md` wherever they conflict.
- Unclear or contradictory spec points go in "Open questions" below. They are not resolved by guessing during a build session.

**Owner decisions (session 0)**
1. **`ARCHITECTURE.md` is retired.** `PROJECT.md` replaces it. All references to it were removed from both docs.
2. **MCP-first is confirmed.** The MCP server is the product. `MEMROUTER.md` §12 now maps its interfaces to MCP tools, and the Python SDK moves to "Later", matching `PROJECT.md`.
3. **Basic memory moves to Phase 2.** Memrouter build step 1 (episodes, write path, basic similarity recall) now ships in Phase 2 with the MCP skeleton, because `recall_context` and `record_outcome` need it. Phase 6 adds the learning features: surprise-based links, spreading activation, decay, consolidation and fear memories.
   - In the docs, Phase 6 also covers MEMROUTER steps 3 (Jev attention filter) and 7 (predictor trust, simulation reuse). These weren't in the owner's list, but they had to go somewhere and weren't moved earlier. See open question 3.
4. **Surprise is a signed prediction error** (`MEMROUTER.md` §5):
   - `outcome_score = 0.6 × success + 0.4 × efficiency`
   - `success` = test pass rate (0 to 1)
   - `efficiency` = average of `min(1, predicted/actual)` over tokens, cost and latency
   - `predicted_score = 0.6 × predicted_success_probability + 0.4`
   - `surprise = outcome_score − predicted_score` (−1 to 1)
   - The sign sets the direction of the link update and the magnitude sets its size. The weights are configurable defaults (added to §14).
5. **Episodes are never deleted.** The sleep job archives low-value episodes to cold storage, which is excluded from retrieval but kept as world-model training data. Pruning applies only to links and the retrieval index. Added an `archivedAt` field to `Episode`.
6. **Tech stack** (`PROJECT.md` §17, new):
   - Python 3.12 with the official MCP Python SDK.
   - Streamable HTTP transport for the hosted server (API key auth); stdio for local development.
   - FastAPI for the API key, credits and usage endpoints.
   - Postgres + pgvector for storage; pytest for tests.
   - Docker for local and cloud runs, with everything containerised. Hosting is decided at launch.
   - Embeddings sit behind an interface; the default is a small open-source model run locally.

**My interpretations while editing the docs (please confirm)**
- The `MEMROUTER.md` §12 tool mapping is my reading of `PROJECT.md` §11:
  - `recall` → `recall_context`; `record` → `record_outcome`.
  - `lookupSimulation` is used inside `evaluate_options` / `submit_consequences`.
  - `predictorTrust` and `consolidate` are internal.
  - `clearFear` → `clear_fear`, human only.
- In the data model, `predicted.success` is now the predicted success probability (0 to 1), and `actual.success` is the test pass rate (0 to 1). I added a `surprise` field to `Episode` so the value is stored with each record.

## Open questions

Items marked **(blocks Phase 1)** need an answer before the next build session can start.

### Background

1. **Existing memrouter project.** `PROJECT.md` §15 says to "reconcile with the existing memrouter project (results not yet shared)". Where is that project? Should it shape Phase 2 and Phase 6, or can the spec be followed as written?
2. **Name clash.** Another GitHub project is already called MemRouter (§15). Should the component keep the name `memrouter` for now?

### Build-order details

3. **Memory in Phases 4 and 5.** Phase 4 says "memory feeds Jev evidence" and Phase 5 includes "memory lookup / reuse past spike results". Proposal: Phases 4 and 5 use the Phase 2 basic similarity recall over episodes, and full simulation reuse, predictor trust and the Jev attention filter arrive in Phase 6. OK?
4. **Task state and memory storage.** Task state and checkpoints must keep working if memrouter is down (MEMROUTER §11). Should they be a separate service or database from memory, or one Postgres with separate tables and a failure-isolated code path?
5. **When does API key auth start?** The hosted server authenticates by API key, but the key, credits and usage endpoints are Phase 7. Should Phases 2–6 use stdio locally with no auth, plus a single static dev key for HTTP?

### Phase 1: benchmark harness

6. **(blocks Phase 1) Baseline agent.** Which host agent is the baseline (Claude Code, Codex, other), which model, and which settings?
7. **(blocks Phase 1) Task subset.** "~50 SWE-bench Lite tasks": how should they be chosen (random with a fixed seed, or curated), and should the list be fixed for all later phases?
8. **(blocks Phase 1) Budget and run environment.** What is the budget per baseline run (tokens and dollars)? Should runs happen locally in Docker, in CI, or on a cloud VM?
9. **Repeated runs.** How many runs per configuration count as "improvement across repeated runs", and how much variance is acceptable?
10. **"Benchmark after every phase".** Phases 1–3 don't change agent behaviour much. Is a no-regression check enough for those phases?

### Decision layer and Jev

11. **Jev access is unverified** (§15): API, pricing, limits. Phase 4 and the memrouter attention filter depend on it. Should the small-LLM comparison scorer (§5) be built first as a fallback behind the same interface?
12. **Thresholds undefined.** There are no values yet for the "clear winner" and "close call" thresholds, the score weights, or crucial-decision detection. §15 already lists this as a spec to write.
13. **"Ask human if high stakes."** What counts as high stakes is only described by examples. A concrete rule is needed.

### Memrouter spec gaps

14. **Surprise edge cases.** What should `efficiency` do when:
    - an `actual` value is 0 (division by zero);
    - a predicted value is missing (for example, no latency estimate);
    - no predictor gave a success probability (for example, a routine step that skipped Jev)?

    Suggested defaults: treat a term as 1 when `actual` is 0, drop missing terms from the average, and fall back to the predictor-trust base rate or 0.5 for a missing probability.
15. **Cold storage medium.** Should archived episodes go in a separate Postgres table or schema, or in object storage (for example, Parquet files)? This depends on the world-model logging format (question 19).
16. **Which embedding model?** The default is a small open-source model run locally. Which one (and what dimensions), or should it be picked and benchmarked in Phase 2?
17. **Decay maths is undefined.** No formulas are given for how `strength` and `stability` change per recall or per "usage opportunity". There is also no definition of a "usage opportunity" or of what counts as a recall being "helpful".
18. **Undefined terms.** "Condition match" scoring for mixed operators (`>`, `=`, ranges) is not defined. Neither is the "severe" failure classification: is it decided by the host, by hooks, or by us?

### Outcomes, privacy and data

19. **Undefined specs.** "Heavy testing" and the data-logging format for the world model are both undefined. §15 lists them, and the logging format is needed from Phase 1/2 ("from day one").
20. **Raw code could be stored.** §12 says to store decision summaries, not raw code. But `Episode.actual.testResults` and the hook-captured test output (§9) can contain code, stack traces and file paths. What sanitisation or redaction is required?

### Host integration

21. **Which hosts first?** Hooks differ between hosts. Should Phase 2 target Claude Code only, or also Codex and Cursor? What does "the host calls us reliably" mean as a number (target invocation rate)?

## Next step

Answer the questions marked **(blocks Phase 1)**: 6 (baseline agent), 7 (task subset) and 8 (budget and run environment). Then start **Phase 1 — Benchmark harness + baseline** (`docs/PROJECT.md` §13) in Python 3.12 with pytest, first writing its short spec (§15, "Benchmark harness: tasks, baseline agent, measurements").
