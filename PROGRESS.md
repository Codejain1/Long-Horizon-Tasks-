# Progress

Updated at the end of every session. See `CLAUDE.md` for the working rules and `docs/PROJECT.md` §13 for the build order.

## Completed

- **Session 0 — repo setup (no platform code).**
  - Moved `PROJECT.md` and `MEMROUTER.md` into `docs/`.
  - Removed the empty root file `Docs`, which held only a newline. It would clash with the `docs/` folder on case-insensitive filesystems (macOS, Windows).
  - Added `CLAUDE.md` (session rules) and this `PROGRESS.md`.
  - Reviewed both docs and logged the unclear and contradictory points below.

## In progress

- Nothing. No build phase has started.

## Decisions made

- Project docs live in `docs/`. The two docs refer to each other by bare filename, and that still works because they sit in the same folder.
- `docs/PROJECT.md` wins over `docs/MEMROUTER.md` wherever they conflict (as `PROJECT.md` itself states).
- Unclear or contradictory spec points go in "Open questions" below. They are not resolved by guessing during a build session.

## Open questions

Items marked **(blocks Phase 1)** need an answer before the next build session can start.

### Missing or conflicting documents

1. **`ARCHITECTURE.md` is missing.** `PROJECT.md` says it is older and loses on conflict. `MEMROUTER.md` calls itself a "companion to `ARCHITECTURE.md`". The file is not in the repo. Should it be added for reference, or should the references be removed?
2. **SDK-first or MCP-first?** `MEMROUTER.md` §12 says memrouter is "exposed as a Python SDK first, then as an MCP server". `PROJECT.md` makes the MCP server the product (§3, §11) and lists the Python SDK under "Later" (§13, item 9). Per the precedence rule, MCP-first wins. Please confirm, and should `MEMROUTER.md` §12 be updated to match?
3. **Existing memrouter project.** `PROJECT.md` §15 says to "reconcile with the existing memrouter project (results not yet shared)". Where is that project? Should it shape Phase 6, or can the spec be followed as written?
4. **Name clash.** Another GitHub project is already called MemRouter (§15). Should the component keep the name `memrouter` for now?

### Build-order dependencies

5. **Phase 2 needs memory that doesn't exist yet.** Phase 2 ships `recall_context` and `record_outcome`, but memrouter is Phase 6. Before Phase 6, should `recall_context` return only task state, and should `record_outcome` just log episodes without learning from them?
6. **Phase 4 and Phase 5 depend on memory.** Phase 4 says "memory feeds Jev evidence" (§5). Phase 5 includes "memory lookup / reuse past spike results" (§6, and `lookupSimulation` in MEMROUTER §12, which is MEMROUTER build step 7). Both come before memrouter (Phase 6). Should these parts be skipped until Phase 6 and then wired in?
7. **Task state and memrouter overlap.** Task state and checkpoints should live outside memrouter so agents can keep working if it is down (MEMROUTER §11). But Phase 3 (task state) comes before Phase 6 (memrouter). Are these separate services or datastores, or one Postgres with separate tables?
8. **Auth and credits before Phase 7.** Access requires a platform API key and credits (§3), but API keys and credits arrive in Phase 7. Should Phases 2–6 run unauthenticated, or with a single dev key?

### Tech stack (not specified anywhere)

9. **(blocks Phase 1) Language and runtime.** `MEMROUTER.md` writes its data model in TypeScript but talks about a Python SDK. What language should the MCP server, services and benchmark harness use? Which test framework?
10. **Hosting and deployment.** Postgres + pgvector is chosen. Where does the platform run during development (local Docker?) and later?
11. **Embeddings.** Which embedding model or provider should episodes and lessons use, and who pays for it?

### Phase 1: benchmark harness

12. **(blocks Phase 1) Baseline agent.** Which host agent is the baseline (Claude Code, Codex, other), which model, and which settings?
13. **(blocks Phase 1) Task subset.** "~50 SWE-bench Lite tasks": how should they be chosen (random with a fixed seed, or curated), and should the list be fixed for all later phases?
14. **(blocks Phase 1) Budget and environment.** What is the budget per baseline run (tokens and dollars), and where do runs execute? SWE-bench needs Docker.
15. **Repeated runs.** How many runs per configuration count as "improvement across repeated runs", and how much variance is acceptable?
16. **"Benchmark after every phase".** Phases 1–3 don't change agent behaviour much. Is a no-regression check enough for those phases?

### Decision layer and Jev

17. **Jev access is unverified** (§15): API, pricing, limits. Phase 4 and the memrouter attention filter (MEMROUTER §6.5) depend on it. Should the small-LLM comparison scorer (§5) be built first as a fallback behind the same interface?
18. **Thresholds undefined.** There are no values yet for the "clear winner" and "close call" thresholds, the score weights, or crucial-decision detection. §15 already lists this as a spec to write.
19. **"Ask human if high stakes"** (§4, §5, §9). What counts as high stakes is only described by examples (spending money, sending messages, deleting data). A concrete rule is needed.

### Memrouter spec gaps

20. **Surprise formula is contradictory.** MEMROUTER §5 says `surprise = |actual_score − predicted_score|, signed by direction`, but an absolute value can't carry a sign. Is surprise signed or unsigned? Also, how is one `score` computed from the multi-field `predicted` and `actual` objects (success, tokens, cost, latency)?
21. **Decay maths is undefined.** No formulas are given for how `strength` and `stability` change per recall or per "usage opportunity". There is also no definition of a "usage opportunity" or of what counts as a recall being "helpful".
22. **Pruning conflicts with training data.** Consolidation prunes "low-value episodes" (MEMROUTER §7). But `PROJECT.md` §6 says to log data in a trainable format from day one, and MEMROUTER §13 says episodes are the training data for the world model. Should pruned episodes be archived for training rather than deleted?
23. **Undefined terms.** "Condition match" scoring for mixed operators (`>`, `=`, ranges) is not defined. Neither is the "severe" failure classification: is it decided by the host, by hooks, or by us?

### Outcomes, privacy and data

24. **Raw code could be stored.** §12 says to store decision summaries, not raw code. But `Episode.actual.testResults` and the hook-captured test output (§9) can contain code, stack traces and file paths. What sanitisation or redaction is required?
25. **Undefined specs.** "Heavy testing" and the data-logging format for the world model are both undefined. §15 lists them, and the logging format is needed from Phase 1/2 ("from day one").

### Host integration

26. **Which hosts first?** Hooks differ between hosts. Should Phase 2 target Claude Code only, or also Codex and Cursor? What does "the host calls us reliably" mean as a number (target invocation rate)?

## Next step

Answer the questions marked **(blocks Phase 1)**: 9 (language and test framework), 12 (baseline agent), 13 (task subset) and 14 (budget and environment). Then start **Phase 1 — Benchmark harness + baseline** (`docs/PROJECT.md` §13), first writing its short spec (§15, "Benchmark harness: tasks, baseline agent, measurements").
