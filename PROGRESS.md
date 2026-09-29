# Progress

Updated at the end of every session. See `CLAUDE.md` for the working rules and `docs/PROJECT.md` §13 for the build order.

## Completed

- **Session 0 — repo setup (no platform code).**
  - Moved `PROJECT.md` and `MEMROUTER.md` into `docs/`.
  - Removed the empty root file `Docs`, which held only a newline. It would clash with the `docs/` folder on case-insensitive filesystems (macOS, Windows).
  - Added `CLAUDE.md` (session rules) and this `PROGRESS.md`. Merged in PR #1.
  - Applied two rounds of owner decisions to both docs (see "Decisions made"). PR #1 was merged before round 1 was pushed, so rounds 1 and 2 go in a follow-up PR.
  - Wrote the Phase 1 spec as `docs/PROJECT.md` §18, including the domains and credentials real runs need.

- **Session 1 — Phase 2: MCP skeleton, task state and memrouter step 1.** Built on request, ahead of Phase 1 (see open question 20).
  - **MCP server** (`src/horizon/server.py`, MCP Python SDK 2.x): `start_task`, `recall_context` and `record_outcome`, with directive descriptions and server instructions. Runs over stdio (local) or streamable HTTP behind a static dev API key.
  - **Task state** (`src/horizon/taskstate/`): goal (verbatim), constraints, plan, progress, decisions and open issues. It is always returned by `recall_context`, and `record_outcome` appends to it.
  - **Memrouter step 1** (`src/horizon/memrouter/`):
    - episodes (MEMROUTER §4 fields, plus `recall_id` and `schema_version`);
    - the write path: record the episode and compute surprise, with every §5 edge case and the low-confidence fallback from similar episodes;
    - basic similarity recall: team-isolated, excludes archived episodes, capped by a token budget, and every recall is logged.
  - **Embeddings:** behind an interface. The default is fastembed (bge-small); the fallback is a deterministic offline hash embedder.
  - **Storage:** Postgres + pgvector, plus SQLite for zero-setup local use. Both pass the same tests.
  - **Failure isolation:** task state keeps working when memrouter fails. Tools report `memory_status: "unavailable"`, and memrouter is retried on the next call.
  - **Claude Code integration** (`docs/CLAUDE_CODE.md`):
    - SessionStart, PostToolUse (Bash) and Stop hooks;
    - a CLAUDE.md snippet;
    - `horizon install-claude-code` (idempotent installer);
    - `horizon stats` (invocation reliability);
    - the hook's real test counts override the model's self-report.
  - Dockerfile and docker-compose (pgvector/pgvector:pg16).
  - Tidied `CLAUDE.md`: the owner's decision rule is now a proper section, and the session-specific text was removed (the approvals are recorded below).
  - **Tests:** 119 pass on both SQLite and Postgres 16 + pgvector 0.6. They include:
    - a real stdio subprocess;
    - HTTP with and without the API key;
    - a simulated Claude Code session (stdio server + hook subprocesses sharing one store).
  - **Real Claude Code check:** `claude mcp list` shows the server as `√ Connected`. No model calls were made.

- **Session 2 — Phase 2 follow-ups** (owner round 4):
  - redaction of decision text (`src/horizon/redact.py`);
  - quiet Stop hook, with a `nudged_at` column on `test_captures` plus an additive migration;
  - `MEMROUTER.md` lists `host` as a prediction source;
  - reliability demo in `demo/reliability/`: 4 tasks, `run.sh`, `report.py`, and a README.
  - Tests: 157 pass on SQLite and Postgres. `run.sh` is tested end to end with a fake `claude` binary that runs the project's real hooks. No real Claude Code session was run from here; the owner runs `demo/reliability/run.sh`.

- **Session 2 — Phase 1: benchmark harness (dry run).** In `src/horizon/bench/` and `bench/`; CLI `horizon-bench select | run | evaluate | report`.
  - **Task selection:** fixed seed; every >4 h task, then 40/35/25% across the other buckets; stratified by repo within each bucket; the first 10 are a stratified smoke set.
  - **Runner:** mini-SWE-agent's `DefaultAgent` with its SWE-bench templates, a 50-step cap and a $1 cap. Environments are Docker when the daemon answers, otherwise a local checkout at the base commit, and a stub repo in dry run.
  - **Records:** one JSONL record per task run (schema v1) with success, tokens (input / output / cache write / cache read), cost, steps and wall time, plus `preds.json`, trajectories and run metadata.
  - **Evaluation:** sb-cli for real runs, a mock evaluator in dry run.
  - **Reports:** per run and across repeats (mean and stdev of the success rate).
  - **Dry run is the default:** mock model, stub repos, mock evaluator, and a test that blocks all network access during a dry run. `real_runs: true` in `bench/config.yaml` is the one switch.
  - A dry-run `--stage full --repeats 2` (100 task runs) takes seconds.
  - Tests: 20 new; 177 pass in total on SQLite and Postgres.
  - **Not done:** the committed 50-task list (`bench/tasks/swebench_verified_50.json`). `huggingface.co` is blocked in this session. See open question 26.

- **Session 3 — Phase 3: checkpoint references and rollback rules** (`PROJECT.md` §8). Stacked on PR #3, which is not merged yet.
  - **Checkpoint references** (`src/horizon/taskstate/checkpoints.py`): a new `PreToolUse` hook on `recall_context` records, just before each decision:
    - a **git** snapshot of the working tree (`git stash create`, or `HEAD` when clean). This doesn't touch the working tree, index, HEAD or stash list;
    - a **Claude Code** checkpoint reference: the latest real user prompt from the session transcript (uuid, time and a redacted snippet), which is what `/rewind` lists.
    - `recall_context` attaches the checkpoint to the task. Each decision in task state records its `checkpoint_id`.
  - **Rollback rules** (`src/horizon/taskstate/rollback.py`): when `record_outcome` reports a failure, it returns `rollback`:
    - **restore:** a `git restore --source=<snapshot>` command, and/or `/rewind` instructions for the user;
    - **failure reason fed back:** a new `failure_reason` parameter, defaulting to the test summary. `recall_context`'s `task_state.retry` lists every failed approach in the streak;
    - **retry limit:** `HORIZON_MAX_ATTEMPTS`, default 3;
    - **escalation:** at the limit the task becomes `escalated`, and the host is told to stop and ask the user. `recall_context(human_guidance=…)` resumes the task.
  - Installer, CLAUDE.md snippet, tool descriptions and `docs/CLAUDE_CODE.md` are updated.
  - **Tests:** 12 new ones, plus an extended simulated Claude Code session (real hook subprocesses and the stdio server). **126 pass on SQLite.** The git restore is checked against real repos. The transcript parser was checked against a real Claude Code transcript, which found and fixed one case: `[Request interrupted by user]` entries.
  - **Not run:** the Postgres variants (48 skipped). This machine has no pgvector or Docker. No real Claude Code session was run either.

- **Session 4 — verification of Phases 1–3** (run locally on the owner's Mac, not in the cloud session).
  - **Test suite:** 204 pass on SQLite **and** on Postgres 14 + pgvector 0.8.1, with no skips. The Postgres variants had never run outside the cloud session before. pgvector was built from source against Homebrew's Postgres 14, and a throwaway cluster ran on `127.0.0.1:55432`.
  - **Phase 1 dry run, end to end:**
    - `select` wrote the **real committed task list** (`bench/tasks/swebench_verified_50.json`, closing open question 26). `huggingface.co` is reachable here. The split is 3 / 19 / 16 / 12 across 9 repos, and re-running `select` reproduces it.
    - `run --stage smoke`: 10 tasks in 3 s.
    - `run --stage full`: 50 × 3 in 45 s, with the report showing mean, stdev and range.
  - **MCP reliability with real Claude Code:** 6 runs × 5 tasks, 30 headless sessions, on Opus 5.5 and Sonnet 5. Details in `demo/reliability/RESULTS.md`.
    - The tools were called at the right moments in 30/30 sessions, and the hooks fired as expected in 30/30. `outcome_recording_rate` was 1.0 in every run.
    - Two misses, both fixed and re-tested:
      - Opus skipped the git restore after a rollback. The instructions are sharper now, and the hook enforces it.
      - The demo's allowlist blocked `python3 -m pytest`.
  - **Demo changes:**
    - a new task 5 (`title_case`) that fails its first attempt, to exercise rollback;
    - sessions isolated from the user's own hooks and MCP servers;
    - the report now measures hook firing and rollback compliance;
    - `CLAUDE_MODEL` selects the host model.
  - **Gaps against the docs, now fixed:**
    - recall ranks by `similarity × conditionMatch` (MEMROUTER §6 step 2) and prefers the narrowest scope (§10);
    - episode `provenance.agent_id` comes from the MCP client info (§4, §11);
    - `record_outcome` can update the task plan (§8);
    - `horizon stats` reports checkpoint counts;
    - the committed task list (§18);
    - README and bench docs are up to date.

- **Session 5 — owner round 5: zero-centred surprise, and judging outcomes against a baseline.**
  - **Surprise** (`src/horizon/memrouter/surprise.py`, `MEMROUTER.md` §5): `0.6 × success_error + 0.4 × mean(efficiency_errors)`, where each efficiency error is `clamp((predicted − actual) / predicted, −1, 1)`. Missing or zero predictions are dropped. The ε setting is gone. A new test shows that 20,000 calibrated predictions average under 0.01.
  - **Baseline** (`src/horizon/taskstate/judge.py`, `PROJECT.md` §9):
    - The PostToolUse hook now stores failing test ids (pytest, unittest, go, cargo, jest, rspec).
    - The test runs between `start_task` and the first `recall_context` become the task's baseline.
    - Outcomes are judged on regressions and target tests. Pre-existing failures are excluded from success and reported in `test_judgement`, and only regressions trigger a rollback.
    - Output that doesn't name every failure, or a task with no baseline run, falls back to the absolute rule.
  - **Demo:** task 5 now produces a real regression (the shared `_cap` helper breaks `sentence_case`), and the report measures `baseline_run`.
  - **Real Claude Code runs 7–10:** run 8 (Sonnet) exposed an ordering gap: it recorded the baseline run before its first recall. Fixed. Runs 9 and 10 (Opus, Sonnet) were clean on every metric, with every outcome judged by the baseline. See `demo/reliability/RESULTS.md`.
  - **Tests:** 16 new (8 of them run on SQLite and Postgres).

- **Session 6 — PR #5 and Phase 4: decision layer** (branch `claude/phase-4-decision-layer`, not yet in a PR).
  - **Merging.** PR #3 and PR #4 had already been merged by the owner, but #4 was merged into #3's branch *after* #3 reached `main`. That left Phase 3, session 4 and round 5 off `main`. Opened and merged **PR #5** (same branch into `main`). `main` passes all 230 tests.
  - **Plan and acceptance criteria (`PROJECT.md` §5, §13 item 4):**
    1. Crucial-decision detection, so routine steps skip the flow.
    2. Scoring with one question per dimension per option, run in parallel. Measurable dimensions are computed in code; judgements come from the scorer.
    3. Configurable weights and thresholds (clear winner, close call).
    4. The second-tie rule: the cheaper or more reversible option, or ask a human if stakes are high.
    5. Memory feeds evidence and fear warnings.
    6. Jev behind a swappable interface, plus the small-LLM comparison scorer.
    7. The scorer's prediction is recorded with the outcome (§9).
    8. The host calls the tool at a crucial choice.

    **All eight are met.** Criterion 6 is met with mocked transports only: there are no API keys. See open questions 37 and 38.
  - **Built:**
    - `src/horizon/decision/`:
      - `scorers.py`: `JevScorer` on the official `typesafe-sdk`, `LLMScorer` on the official `anthropic` SDK with structured outputs, both behind one `Scorer` interface;
      - `layer.py`: the questions, composite, rules and fallback.
    - The **`evaluate_options`** MCP tool. The tool description, server instructions, SessionStart text and CLAUDE.md snippet all tell the host to call it before a crucial choice.
    - The `[decision]` extra, settings (`HORIZON_SCORER`, weights, thresholds), and docs in `docs/CLAUDE_CODE.md`.
  - **Real Claude Code:** a new demo task 6 contains a crucial choice. Opus 5.5 and Sonnet 5 both called `evaluate_options` before their first edit, with four sensible options each. One finding, now fixed: `unscored` must not name a `chosen` option (details in `demo/reliability/RESULTS.md`).
  - **Tests:** 258 pass on SQLite and Postgres (16 new for Phase 4). The Jev and LLM adapters are tested through the real SDKs over a mocked HTTP transport, which checks the request shape and response parsing.

- **Session 7 — live Jev.** The owner provided a TypeSafe API key. It is kept out of the repo and passed only as `TYPESAFE_API_KEY`.
  - **Live calls:** `jev-1.13.0`, 0.45–0.71 s per decision. Three hand cases behaved sensibly: a routine variable name, a clear storage winner, and a safe production migration.
  - **Demo task 6 with Jev behind `evaluate_options`, on Opus 5.5 and Sonnet 5:** both hosts called it before editing, got `clear_winner` → sqlite3, followed it, and passed the tests.
  - **Two fixes from the live runs:**
    - the high-stakes question was reworded (a false positive on caches; true positives unchanged);
    - the scorer's prediction now wins over the host's own guess for the evaluated option, so episodes record `source: jev`. Verified in a rerun: predicted 0.87, surprise +0.13.

- **Session 8 — Phase 5: consequence checking** (branch `claude/phase-5-consequences`, stacked on the Phase 4 branch).
  - **Acceptance criteria (`PROJECT.md` §4, §6; MEMROUTER §12–13), all met:**
    1. A close call runs the chain cheapest first:
       - past spike results reused from memory (a decision can be fully settled from memory);
       - try-and-rollback when every close option is cheap to undo, the stakes aren't high and a git checkpoint exists;
       - otherwise a consequence plan: static checks, then one spike per untested option, with what to build, what to measure and a time budget.
    2. **`submit_consequences`** takes structured results (pass/fail, test counts, numeric metrics, one redacted note) and runs a second scoring pass with them as evidence. That pass includes the §6 consequence questions: breaks existing tests? noticeably costlier?
    3. Failed checks eliminate an option; spike metrics replace the estimates; a second tie never loops (cheaper, more reversible, or ask a human if high stakes).
    4. Spike results are stored in memrouter (`lookup_simulation`, MEMROUTER §12) for reuse.
    5. Every decision goes to the **world-model decision log**: versioned JSON with the state, every raw scorer answer from both passes, consequences, the final decision, and later outcomes. `horizon export-decisions` writes JSONL.
  - **Tests:** 290 pass on SQLite and on Postgres 14 + pgvector (32 new for Phase 5).
  - **Live verification:**
    - Jev flipped the pass-2 winner correctly when spike evidence flipped.
    - A real Sonnet session given a forced close call followed the whole protocol: it built multi-process stress-test spikes and submitted structured metrics (dbm lost 28/360 writes and was eliminated).
    - Three fixes came out of these runs: the spike location, a redaction false positive, and the pass-2 confidence gate (see `demo/reliability/RESULTS.md`).

- **Session 9 — Phase 6: memrouter learning** (branch `claude/phase-6-memrouter-learning`, stacked on Phase 5).
  - **Acceptance criteria (MEMROUTER.md §5–§9, build steps 2–7), all met with tests:**
    1. **Surprise-based link learning:** `Δweight = lr × surprise × signalWeight`, × 0.5 when low-confidence. New semantic links start at 0.1.
    2. **Spreading activation:** 2 hops, × 0.5 per hop, scaled by link weight.
    3. **Jev attention filter** within the token budget.
    4. **Spaced-repetition decay and pruning.**
    5. **The sleep job:** replay, extract, merge, archive to `episodes_archive`, prune, refresh, Parquet export.
    6. **Conditions and reconsolidation.**
    7. **Fear memories:** created instantly, always surfaced, weakened visibly, cleared by a human only.
    8. **Predictor trust** per source and task type.
  - **Headline test:** a simulated agent repeats one decision where stale successes sit closest to the query. **Top-3 precision rises 0.00 → 0.33 → 0.67 → 1.00 by round 4 with learning, and stays at 0.00 for 11 rounds without it** (Phase 2 similarity-only memory). Both are asserted in `tests/test_learning.py`.
  - **Live Jev as the attention filter:** out of 5 candidates it kept only the 2 relevant memories (what worked, and the warning), dropping three that shared "public REST api". It took 0.6 s.
  - **Tests:** 328 pass on SQLite and on Postgres 14 + pgvector (19 new learning tests, 38 with both backends). One Postgres-only bug was fixed: pgvector returns `Vector` objects.

- **Session 10 — Phase 7: inspection, approvals, accounts** (branch `claude/phase-7-inspection-approvals`, from `main`; PRs #6 and #7 were merged in the right order, so `main` has Phases 1–6).
  - **Acceptance criteria (PROJECT.md §3, §10, §11; MEMROUTER §9, §11, §12), all met:**
    1. **Inspection MCP tools:** `explain_decision`, `show_memories`, `delete_memory` (archives episodes, deletes lessons, audit-logged, refuses fear lessons) and `clear_fear` (a named human only).
    2. **Human approvals via MCP user-input requests** for high-stakes ties, rollback escalations, memory deletion and clearing fears. Both protocol generations are supported: ≤ 2025-11-25 inline elicitation, and the 2026-07-28 `InputRequiredResult` with HMAC-signed state. Clients that can't ask fall back to the previous behaviour, and every outcome is logged.
    3. **Accounts:**
       - teams and hashed API keys;
       - a credit ledger with a free starter grant;
       - per-call metering on the hosted transport, with per-team isolation;
       - `horizon create-team` and `horizon add-credits`.
    4. **Minimal web page** (FastAPI): key sign-in, a session cookie, credits, keys (create/revoke), usage, the credit history and savings per session, plus a JSON API.
  - **Tests:** 17 new Phase 7 tests (both MCP protocol paths, forged-state rejection, metered hosted MCP over real HTTP, the web page and API, the CLI).
  - **Real Claude Code check** (Sonnet 5, headless): it used `show_memories` correctly and refused on its own to clear a recent fear lesson without the user's confirmation. When told "I confirm, my name is Kartik", its `clear_fear` call got a user-input request that headless mode cancelled, so nothing was cleared: an agent can't approve on the user's behalf.

- **Session 11 — Phase 8: launch preparation** (branch `claude/phase-8-launch`, from `main`, which has Phases 1–7; PR #8 merged).
  - **README** rewritten around a measured setup of under 5 minutes (≈ 1 min cold from a fresh clone, plus the host's one-time approval).
  - **Codex:**
    - `horizon install-codex` writes `.codex/config.toml` (tools pre-approved, env passthrough), `.codex/hooks.json` (Claude-format hooks work unchanged) and the AGENTS.md snippet; `docs/CODEX.md` documents it;
    - **verified in real `codex exec` sessions**: tools, hooks, baseline and checkpoints all worked;
    - the runs found and fixed two issues: MCP calls needed pre-approval, and the server used a different database until `env_vars` was added.
  - **One snippet for both hosts** (`agent.snippet.md`, into CLAUDE.md and AGENTS.md), now covering the inspection tools.
  - **Data and privacy policy:** `docs/PRIVACY.md`, served at `/privacy` (a test keeps the two copies identical). Every storage path was reviewed (below).
  - **Docker:** all hosted extras, a non-root user, the embedding model prefetched, `/healthz` with a Docker HEALTHCHECK, a nightly `sleep` service, Postgres not published, `.env.example`, and `docs/HOSTING.md`. The image build is unverified (no Docker here); the same steps passed in a clean Python 3.12 environment.
  - **Hosted hook bridge (a launch blocker the end-to-end test found):**
    - Before this, hooks wrote straight to the database, which can't work against a hosted server.
    - Now, with `--hosted URL`: hooks parse locally and send only facts to `/api/hooks/*` with the team key, and the project is a hashed key sent in an MCP header. `install-* --hosted` writes both host configs.
  - **End-to-end test** (`tests/test_e2e.py`): the whole flow against the hosted server over real HTTP, from team creation to the account page's savings.
  - **Tests:** 367 pass on SQLite and Postgres (8 new). A test-isolation leak was fixed: the sleep job's exports had been written to the real `~/.horizon/exports` (29 test files). Those files, and one demo task a Codex run put in `~/.horizon/horizon.db`, were confirmed as artifacts and removed.
  - **Launch checklist:** `docs/LAUNCH.md` separates what's ready from what waits on the owner.

- **Session 13 — the world model, wired in** (`PROJECT.md` §6: "kept in the architecture from day one behind the same interface; swapped in only when it beats the stand-ins"; "log data in a trainable format").
  - **Audit.** Only the logging existed (the decision log, JSONL and Parquet). There was no interface, nothing learned from the logs, and no gate. Actual tokens and latency were almost never recorded, because hosts can't see their own usage. So the world model had no efficiency targets, and the efficiency half of surprise was dead.
  - **Acceptance criteria, all met with tests:**
    1. A `WorldModel` interface, with `HORIZON_WORLD_MODEL_CLASS` to plug in another model.
    2. A first learned model, `kernel-v1`: option-aware, learns from every episode including archived ones, predicts success, tokens, cost and latency, keeps an incremental in-memory index, and drops purged episodes.
    3. The gate: a time-ordered replay against the stand-ins' recorded predictions, with a one-sided paired test, plus `horizon eval-world-model`.
    4. Wiring into `evaluate_options` (`auto` by default: shadow until the gate passes). When active it fills missing estimates and settles confident close calls without spikes (`settled_by: "world_model"`), and its accuracy is tracked as predictor source `world_model`.
    5. Real attempt usage: the PreToolUse hook (now also on `record_outcome`) sums tokens and time since the last recall from the transcript, including over the hosted bridge (`/api/hooks/usage`), and `record_outcome` uses them.
    6. Parquet export gains flat token, cost and latency columns, and the docs and privacy policy are updated.
  - **Measured:** at 5,000 episodes, the cold index load takes 89 ms, a forecast for 5 options takes 7 ms warm, and replaying 2,000 episodes for the gate takes 0.14 s.
  - **Tests:** 32 new (`tests/test_world_model.py`), **426 pass on SQLite and on Postgres 14 + pgvector.**
  - **Not done:** a Dreamer-style latent model. There's no data to train one (0 real episodes on this machine), and §13 keeps it "later". The interface, the logs and the gate are what it needs when data exists.

## In progress

- Nothing. Phases 1–8 are built. What remains needs the owner: real benchmark runs, the go-ahead to publish, hosting and pricing.

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
4. **Surprise is a signed prediction error** (`MEMROUTER.md` §5; the formula below is superseded by round 5, decision 23):
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
7. **Surprise edge cases** (`MEMROUTER.md` §5; superseded by round 5, decision 23):
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

**Owner decisions, round 3**
14. **Phase 1 defaults approved** (were open questions 6–9):
    - Docker when available, with per-task local setup as the fallback.
    - The proposed difficulty weights.
    - The first 10 of the 50 tasks form the smoke run.
    - Per-token prices go in config if litellm lacks `claude-sonnet-5`.
15. **Decision rule** added to `CLAUDE.md`: choose reasonable defaults and log them, and stop only for credentials or money, hard-to-reverse changes, contradictions with the docs, or true blockers.

**Owner decisions, round 4** (answers to Phase 2 questions)
16. **Defaults approved:** open questions 4, 5, 14, 18 and 19 keep the defaults used in Phase 2.
17. **Phase 1 is next:** build the harness (dry run, no paid calls).
18. **Reliability measurement:** Claude Code cloud sessions run on the owner's subscription, so no API credits are needed. A demo repo with 3–5 small tasks and a script measure how reliably the tools get called. Built as `demo/reliability/` (4 tasks).
19. **`host` is a valid fourth prediction source.** `MEMROUTER.md` §4 and §5 are updated.
20. **Basic redaction:** strip code blocks and code-like lines from decision descriptions and cap their length. We promise not to store raw code.
    - Built as `src/horizon/redact.py`. It covers `situation`, `chosen`, `alternatives`, `reason`, `progress_note`, `open_issues`, `plan`, `constraints` and string condition values.
    - The caps are: situation 500 characters, options 200, notes 300, condition values 100.
    - The **goal stays verbatim** (`PROJECT.md` §8). It is the one host-written field that is not redacted.
21. **Embedding fallback stays.** To download `BAAI/bge-small-en-v1.5`, fastembed 0.8 pulls the Hugging Face repo `Qdrant/bge-small-en-v1.5-onnx-Q` through `huggingface_hub` with `hf_xet`. The domains to allow are:
    - `huggingface.co` (API and file resolution);
    - `*.hf.co` (file CDN and Xet storage: `cas-bridge.xethub.hf.co`, `cas-server.xethub.hf.co`, `transfer.xethub.hf.co`, `cdn-lfs.hf.co`);
    - `cdn-lfs.huggingface.co` and `cdn-lfs-us-1.huggingface.co` (the older LFS CDN, used when Xet is off).
    - This model has no Google Cloud Storage source in fastembed 0.8.
22. **Stop hook is quiet.** It triggers only when an active task has an unrecorded outcome: a test run from **this session**, after the task started, that no `record_outcome` has used. It nudges at most **once per test run** and never loops.

**Phase 1 decisions (defaults chosen under the decision rule)**
- **The harness lives in the same package** (`horizon.bench`, CLI `horizon-bench`) with an optional `[bench]` extra (mini-swe-agent, sb-cli, datasets), so the MCP server doesn't depend on litellm.
- **Built on the Phase 2 branch and PR (#3):** this session is limited to that branch.
- **Cost comes from `model.prices` in `bench/config.yaml`** ($2 / $10 / $2.50 / $0.20 per MTok for input / output / cache write / cache read, the `claude-sonnet-5` list prices). litellm 1.102 does list the model at the same prices; its figure is recorded as `litellm_cost_usd` for cross-checking. One source drives both the cap and the reports.
- **The caps are mini-SWE-agent's own `step_limit` and `cost_limit`.** Both are checked before each call, so a task can overrun $1 by at most one call.
- **mini-SWE-agent's stock SWE-bench prompts and settings** (`swebench.yaml`) are used unchanged, apart from the caps. This keeps the baseline standard and comparable.
- **Local fallback:** `git clone` at `base_commit`, then `pip install -e . pytest` into a venv. This is best effort, and the result is recorded per task (`env_setup`). The SWE-bench per-repo install specs are not used.
- **Dry run without a committed list** uses a synthetic 500-task pool shaped like SWE-bench Verified (same bucket sizes and repo mix). Real runs refuse to start without the committed list and both API keys.
- **Mock model:** tokens imitate a SWE-bench trajectory with a growing, mostly cached prompt. Every 4th task never submits, so the caps get exercised. The **mock evaluator** is deterministic with some per-repeat variance, and reports are marked "DRY RUN".
- **Repeats:** the full stage defaults to 3 repeats (`execution.repeats`) and the smoke stage to 1.
- **Result records are the world-model log format** (open question 17): one JSON line per task run, `schema_version: 1`.
- **`PROJECT.md` §18 domain table updated:** the sb-cli host is `api.swebench.com`, and a row was added for Docker Hub (`registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`) for the task images.

**Phase 2 decisions (defaults chosen under the decision rule)**
- **Package and SDK:** package `horizon` in `src/`, with the CLI `horizon`. The MCP Python SDK is 2.x (`MCPServer`), since 2.2 is current.
- **SQLite backend alongside Postgres:** makes local Claude Code prototyping zero-setup. Both run the same tests. Postgres + pgvector remains the hosted store.
- **Task state and memory storage** (default for open question 4): same database, separate tables and separate connections. Memrouter is created lazily and retried, so a memrouter failure never blocks task state.
- **Auth before Phase 7** (default for open question 5): stdio has no auth. HTTP needs `HORIZON_DEV_API_KEY` as a Bearer token or `X-API-Key`.
- **Host scope** (default for open question 19): Claude Code only, as requested.
- **Embedded text is the situation plus conditions, not the chosen option:** at recall time the choice isn't known yet, so this keeps the write side and the read side symmetric.
- **Offline hash embedder fallback:** Hugging Face is blocked in the cloud session, so the bge model can't download. Each episode stores `embedding_model`, and recall only compares vectors from the same model, so the two embedders never mix.
- **Recall parameters:** top-50 candidates by cosine similarity, a minimum similarity of 0.2, at most 8 items, and a 1,500-token budget estimated at about 4 characters per token.
  - There is no condition-match scoring yet (open question 16). Conditions are stored and embedded as text.
- **"Similar episodes" for the fallback success rate:** the top 10 in the team with similarity ≥ 0.5, excluding archived episodes.
- **Surprise in Phase 2:** computed and stored on every episode (write path step 2). Link updates wait for Phase 6.
  - The surprise weights are normalised to sum to 1, which keeps the range at −1..1.
  - When predicted and actual are both 0, the ratio is 1 (default for open question 14; it follows the "actual is 0 → ratio 1" rule).
- **`predicted.source` gains a `"host"` value:** in Phase 2 there is no Jev or simulation, so predictions come from the host LLM or the memory fallback (`"memory"` is not set yet; see open question 22).
- **Test results are stored as counts only** (default for open question 18): passed, failed, total and runner, plus the command truncated to 300 characters. Raw output is never stored.
- **Hook-captured test counts override the host's own report.** A capture belongs to a task when it comes from the same project directory after the task started. All pending captures are consumed on `record_outcome`.
- **Flat tool parameters** (`predicted_success`, `tests_passed`, …) instead of nested objects: these are easier for host LLMs to fill in correctly.
- **`record_outcome` also updates task state:** it appends a decision and a progress line, can replace the open issues, and marks the task done with `task_complete`.
- **Every recall is logged** (`recall_log`) **and every tool call is logged** (`tool_calls`). The recall log supports Phase 6 link learning; the call log feeds `horizon stats`.
- **Stop hook blocks at most once per stop** (it respects `stop_hook_active`) and only when an active task has unrecorded test runs.
- **Installer:** uses the absolute Python path in hooks and `.mcp.json`, and adds `enabledMcpjsonServers`.
  - Claude Code still asks for one-time approval of project `.mcp.json` servers. The docs show `claude mcp add -s local` as the prompt-free alternative.
- **No `episodes_archive` table yet:** the sleep job that fills it is Phase 6. The `archived_at` column exists, and recall excludes archived episodes.
- **World-model logging** (open question 17, partly): each episode carries `schema_version: 1`.

**Phase 3 decisions (defaults chosen under the decision rule)**
- **Checkpoints are captured by a hook, not the model:** `PreToolUse` with matcher `mcp__horizon__recall_context`, the moment before each decision. This matches the Phase 2 test-capture pattern: the hook writes to a `checkpoints` table and the next `recall_context` for that directory claims it. The server never reads the repository (§12); only commit ids are stored.
- **Git snapshot = `git stash create`, falling back to `HEAD`.** It captures uncommitted tracked changes without modifying anything. Untracked files aren't captured.
- **Restore = `git restore --source=<snapshot> --staged --worktree -- :/`.** It resets tracked files, deletes tracked files added since, and leaves HEAD, history and untracked files alone. It is non-destructive to commits, and the failed attempt stays in Claude Code's own checkpoints.
- **Snapshot commits aren't pinned under `refs/`**, so `git gc` could prune one after about 2 weeks. That's fine for in-task rollback. (`ponytail:` note in the code.)
- **Claude Code checkpoint = the latest real user prompt** in the transcript. `/rewind` can only be run by the user, so it is given as an instruction; git is what the host runs itself.
- **Failure = test pass rate below 1.0** (`HORIZON_ROLLBACK_BELOW`). See open question 29.
- **Retry limit = 3 consecutive failed attempts per task** (`HORIZON_MAX_ATTEMPTS`): two rollbacks, then escalation. Streaks are counted per task, not per situation, because the host words the situation differently each time.
- **Every rollback in a streak targets the checkpoint from before the first failure.** If the host skips a restore, the broken tree can't become the new baseline.
- **Escalation = task status `escalated` plus a human-guidance resume on `recall_context`.** No new tool was added; MCP approvals (elicitation) are Phase 7. Escalated tasks still count as open for the SessionStart and Stop hooks, and SessionStart flags them.
- **`task_complete: true` wins over a failing pass rate:** there is no rollback when the host says the task is done.
- **At most 20 checkpoints are kept per task**, plus the rollback target.
- **No data migration:** the new table is created if missing, and the new task-state fields have defaults, so existing stored tasks load unchanged.

**Session 4 decisions (defaults chosen under the decision rule)**
- **Postgres tests run against a throwaway local cluster** (Postgres 14 + pgvector 0.8.1 built from source). Docker isn't installed on this Mac.
- **Reliability sessions are isolated** (`--setting-sources project,local --strict-mcp-config`). Otherwise the owner's own hooks and MCP servers (such as a user-scope memory server) would confound the measurement.
- **Restore enforcement lives in the existing PreToolUse hook**, not in new hooks on Edit/Write. That hook already snapshots git, so it compares the snapshot's tree with the rollback target. It denies one recall at most per rollback, so a host that can't restore is never stuck. It checks git only, because a Claude Code `/rewind` can't be verified.
- **conditionMatch default:** current conditions are treated as facts and stored ones may be predicates. A key is compared when one side is `=`. The factor is 1.0 when every comparable key matches, 0.25 when none do and 0.75 when nothing is comparable. Two predicates on the same key are skipped (this settles the mixed-operator part of open question 16). It ranks results and never filters them out.
- **Scope preference:** an episode from another project in the team scores × 0.85. Same-project episodes are preferred but never exclusive.
- **Episode provenance** is `client_info` `name/version` from the MCP initialize handshake (for example `claude-code/2.1.283`). It is client-supplied, so it's for tracing, not identity. Subagents share one connection, so they can't be told apart yet.
- **`plan` on `record_outcome` replaces the plan**, like `open_issues`, and is redacted the same way.

**Owner decisions, round 5**
23. **Open question 34 (surprise bias):** replaced by the zero-centred formula above. `MEMROUTER.md` §5 and §14 are updated. The low-confidence rule for a missing success probability is unchanged.
24. **Open questions 29 and 35 (pre-existing failures):** snapshot the failing tests at the start as a baseline. Success excludes baseline failures, rollback triggers only on regressions, and pre-existing failures are reported separately. Added to `PROJECT.md` §9.
25. **Open questions 11–13:** my defaults are approved unless they contradict the docs. They are set in the Phase 4 plan.
26. **Merge PR #3 and PR #4 once tests pass, then start Phase 4 on a new branch.**

**Session 5 decisions (defaults chosen under the decision rule)**
- **The baseline comes from the host's own test run**, which `start_task`'s `next`, the tool description and the CLAUDE.md snippet all ask for: "run the full suite once, before any edit". The server never runs tests itself (§12), and the hook doesn't know the project's test command.
- **Baseline window = test runs after `start_task` and before the first `recall_context`.** Whichever of `recall_context` or `record_outcome` comes first takes it. Run 8 showed that hosts sometimes record the baseline run itself. This relies on recall coming before the first edit, which held in 50/50 real sessions.
- **Target tests = `start_task`'s `target_tests` plus test files named in the goal** (a regex for test file paths). Target tests always count, even though they fail at the baseline. Without targets, a task's own failing tests would be excluded and success would look perfect.
- **Regression = failing now and not failing at the baseline.** This includes new tests the host wrote that fail: only failing ids are visible in test output, so "passed at baseline" can't be checked.
- **Absolute fallback** when the output didn't name every failure (for example `| tail -1`), when counts were host-reported, or when there's no baseline. `HORIZON_ROLLBACK_BELOW` now applies only there.
- **Target tests still failing without a regression is not a rollback**, per rule 24. It lowers success and shows up under `target_tests_failing`, but nothing is restored.
- **Test ids are stored with captures** (at most 500 per run) as names only, never output. `test_captures.failing` is an additive, nullable column, with the same migration pattern as `nudged_at`.
- **Surprise ε removed** (`HORIZON_SURPRISE_EPSILON`): the new formula divides by the prediction, and zero predictions are dropped.
- **Known flake:** one full-suite run on macOS aborted at interpreter exit (`libc++abi … recursive_mutex lock failed`), most likely onnxruntime (fastembed) tearing down. Three reruns were clean, and it doesn't come from test logic.

**Phase 4 decisions (defaults under the decision rule; open questions 11–13 approved in round 5)**
- **Open question 11:** the **small-LLM comparison scorer is built alongside Jev** behind the same interface, and `HORIZON_SCORER` picks `jev`, `llm` or `none`. The default is `none` until the owner enables one; then options are ranked on estimates only and nothing is chosen.
- **Open question 12, thresholds:**
  - **crucial** if any of *hard to reverse*, *shapes many later steps* or *real cost* is ≥ 0.5 (an "any serious signal" rule, not a weighted average);
  - **clear winner** if the composite lead is ≥ 0.10 and the leader's scorer confidence is ≥ 0.5;
  - otherwise a **close call**.
- **Open question 12, weights:** success 0.30, compatibility 0.20, architecture fit 0.20, cost 0.15, tokens 0.10, latency 0.05. They are renormalised over the dimensions available. Measured dimensions count only when every option has an estimate: best = 1, others = best / value.
- **Open question 13, high stakes:** the scorer's *would any option spend money, message people, or delete or overwrite data* ≥ 0.5 (the §9 examples), **or** a severe past failure among the recalled memories. High stakes only matters on a close call: a clear winner doesn't need a human.
- **One scorer request per decision**, including the crucial-detection questions. §16 worries about per-decision overhead, but the host calls this tool only at decision points, and one parallel request is faster than two sequential ones.
- **Accuracy is scored as a Noul**, *will this option work?*, so it doubles as the calibrated predicted success probability recorded on the episode (source `jev` or `llm`). The prediction is used when `record_outcome`'s `chosen` matches the evaluated option (case-insensitive containment). **It wins over the host's own `predicted_success`** (session 7: hosts always send one), because §9 asks to record what Jev predicted and Phase 6 predictor trust needs Jev's forecasts.
- **`llm` is a new prediction source** (`MEMROUTER.md` §4 updated), so Jev's predictor trust (Phase 6) is never mixed with the comparison scorer's.
- **The LLM scorer model is `claude-haiku-4-5`**, overridable: §5 asks for a *small* LLM. It answers through a JSON-schema structured output and gives no confidence, so its answers never block a clear winner.
- **A scorer failure or missing key degrades to `unscored`**, the same failure isolation as memrouter. The scorer is created lazily, and creation is retried on the next call.
- **Demo has 6 tasks**, one more than the owner's "3–5": task 6 is the only one with a crucial choice. Run it alone with a one-task `tasks.json`, as done here.

**Phase 5 decisions (defaults under the decision rule)**
- **Spike format** (§15 "spike format, measurements, structured result schema"):
  - **A plan spike** is `{option, build, measure[], budget_minutes}`.
  - **A submitted result** is `{option, static_checks[{name, passed}], spike{ran, passed, tests_passed, tests_failed, metrics{name: number}, duration_s}, notes}`.
  - Numbers and pass/fail only, plus one note that is redacted and capped. Metric names are capped at 40 characters.
- **Static checks are generic and host-chosen:** the dependency resolves, licence and platform fit, lint/type config. The host doesn't know the options' code yet, and the server never sees the repo (§12).
- **Spikes run under `.horizon/spikes/`** in the project, which the installer adds to `.gitignore`. The host needs no extra permission, and git and checkpoints never see the files. Seen live: `/tmp` cost 10 permission denials.
- **Try-and-rollback is used only when all three hold:** every close option's reversibility is ≥ 0.7, the task has a git checkpoint (so Phase 3 can actually restore), and the stakes aren't high. The host then implements the top option, and a regression rolls back to the next option in `try_order`.
- **High stakes no longer asks a human at pass 1.** §4's flow checks consequences first and asks only if the options are still tied.
- **Hard evidence overrides scores.** An option that failed a static check or its spike is eliminated, unless every option failed; then all stay in, so the decision is never stuck.
- **Pass 2 re-asks the per-option questions with `consequences` in the state**, plus two consequence Nouls. They become the `no_regressions` (weight 0.20) and `relative_cost` (0.10) dimensions. Pass 1 lacks them, so its weights are renormalised.
- **After evidence, a lead of at least twice the clear margin stands even when a Score confidence is below 0.5.** In pass 1, low confidence means "go check"; in pass 2 there is nothing left to check (seen live: a 0.30 lead with confidence 0.47).
- **Spike reuse needs situation+option similarity ≥ 0.85 and a matching option label.** Metric: `settled_by: "memory"` counts toward §14 "share of close calls settled from memory".
- **`submit_consequences` runs once per decision.** A second call is rejected (§5: don't loop). The decision must belong to the task.
- **Decision log in the task store's database** (table `decisions`): it's task-scoped and must keep working when memrouter is down. Spike results live in memrouter (`spike_results`), because MEMROUTER §12 assigns `lookupSimulation` there.
- **An outcome is attached to the decision when the implemented option is any of the decision's options**, not only the chosen one, with `followed_decision` recorded. Training data then includes overrides. Unrelated later outcomes aren't attached.
- **JSONL, not Parquet, for now.** MEMROUTER §14 ties the Parquet export to the Phase 6 consolidation run.
- **Redaction (Phase 2 bug found live):** the symbol-ratio rule needs at least 3 symbols, so labels like `dbm (stdlib)` survive.

**Phase 6 decisions (defaults under the decision rule; these settle open question 15 and the mixed-operator part of 16)**
- **Strength and stability are kept for every memory** (episodes too, not only lessons and strategies) in `memory_state`, so the same learning ranks all three kinds. Strength starts at 0.5 and stability at 1.
- **Candidate score** = similarity × conditionMatch × scope × (0.2 + 0.8 × strength). The strength factor is bounded, so learning can reorder memories but never erase a highly similar one. (An unbounded base-level term swamped relevance in the owner's earlier memrouter experiments.)
- **Credit assignment:** only recalled memories about the **option the host implemented** "fed" the decision and learn from its surprise. Other recalled memories had a usage opportunity and didn't help: strength − 0.02 / stability.
- **"Helpful" (open question 15)** = surprise ≥ 0 **and** success ≥ 0.5. A helpful recall multiplies stability by 1.5 (capped at 20, so nothing is permanent).
- **Strength change** = lr × signal × surprise, the same rule as links. A loss is divided by √stability, so proven memories decay slower.
- **Hebbian co-success links** join the fed memories and the new episode. They start at the semantic weight (0.1) and move by the same Δ. Links with weight 0 or below never spread activation. Pruning happens in the sleep job.
- **Spreading activation** traverses semantic, co-success and derived-from links in both directions, never `contradicts`. Memories reached only through links get the condition and scope factors too. Archived episodes drop out.
- **Attention filter:** one Jev Noul per shortlisted memory ("would it help decide `situation` well?"), kept at ≥ 0.5 in descending order. Without Jev, or on failure, the activation order stands.
- **Sleep job:**
  - replays the last 1,000 episodes and clusters them by same option plus similarity ≥ 0.8;
  - 3+ episodes at ≥ 80 % success become a strategy, and at ≤ 30 % a failure lesson; mixed clusters are left to reconsolidation;
  - conditions = those shared by every episode in the cluster;
  - merges into an existing lesson at similarity ≥ 0.85, keeps the newest 3 evidence episodes in retrieval and archives the rest;
  - runs inline every 200 episodes (failure-isolated), or from `horizon consolidate` or cron.
- **Reconsolidation:** a contradicting outcome on a recalled lesson or strategy about the same option looks for a condition that every supporting episode shares and the new one doesn't:
  - for numbers, the bound becomes `<=` the maximum or `>=` the minimum;
  - for categories, it becomes `=` the shared value.
  - With no such condition, contradictions are counted, and at 2 the lesson's strength halves and it gets a `contradicts` link.
- **Fear lessons:**
  - created at strength 1.0 and maximum stability;
  - surfaced when similarity ≥ 0.5 and conditions don't rule them out, even beyond the token budget or the attention filter's choice;
  - a success of ≥ 0.8 with the same option under matching conditions multiplies strength by 0.7 and records it;
  - cleared only through `horizon clear-fear --by <name>`, a CLI a human runs. The MCP `clear_fear` tool is Phase 7.
- **Predictor trust:** after every episode with a prediction, per source and `task_type` (a condition; default "general"): calibration (mean predicted − mean actual), accuracy (1 − mean absolute error) and Brier score. When memory's history stood in for a missing prediction, memory is the source scored.
- **Parquet** needs the new `[export]` extra (pyarrow). One flat row per episode, archived ones included, plus the full JSON.

**Phase 7 decisions (defaults under the decision rule)**
- **`delete_memory` never deletes an episode.** "Episodes are never deleted" (round 1, decision 5) wins, so an episode is archived (out of retrieval, kept as training data) and its links are removed. Lessons and strategies are deleted, with a snapshot kept in `memory_removals`. Hard deletion for privacy would be hard to reverse and would contradict decision 5, so it's an open question (47).
- **Human-only actions are enforced by where the answer comes from.** `clear_fear` accepts only a confirmation that arrives through the client's user-input request. Tool arguments can't carry it (the agent writes those), and a client that can't ask gets an error pointing to the `horizon clear-fear` CLI.
- **The 2026-07-28 round trip keeps round 1's result in `request_state`, HMAC-signed.** The tool body would otherwise run twice and redo work (a second `submit_consequences` is rejected). The state is bound to the tool, the team and the arguments, and the secret is per process unless `HORIZON_STATE_SECRET` is set.
- **Approvals Horizon asks for:** high-stakes ties after consequence checks (§4 and §9: money, messages, data), rollback escalations (§8), memory deletion, and clearing fears. The user's choice becomes the decision (`human_choice`, with the approver in the decision log), and the episode still records Jev's prediction for that option.
- **Accounts share the task store's database.** Only SHA-256 hashes of keys and session tokens are stored. Keys look like `hzn_` + 32 random bytes, and a key is shown once. The web session is an HttpOnly, SameSite=Strict cookie (Secure over https), valid for 7 days.
- **Credit prices are placeholders** (§15 leaves pricing open): 1 per call for `start_task`, `recall_context` and `record_outcome`; 5 for `evaluate_options` and `submit_consequences` (Jev plus orchestration); inspection and approvals free; 1,000 free starter credits. There are no payments (they would need money and a provider), so operators top up with `horizon add-credits`.
- **There's no sign-up page.** An operator creates teams with `horizon create-team` until hosting and pricing are decided at launch (§17).
- **Metering applies to the hosted HTTP transport only.** Local stdio is free and unauthenticated (the user runs everything), and the static dev key stays unmetered for local HTTP.
- **Savings per session are counted events only**, with no invented token savings. "Tokens saved" is reported only where past spikes recorded their token cost. With 2026-07-28 (stateless HTTP, no `mcp-session-id`), a Horizon task stands in for the session.
- **The web page is server-rendered HTML** with no JavaScript and a dark-mode style. It needs the `[web]` extra (FastAPI, python-multipart). The MCP app is mounted under it, and its lifespan runs the MCP session manager.

**Phase 8 review against PROJECT.md: what was missing or inconsistent, and what was done**

| § | Finding | Resolution |
|---|---|---|
| 11 | No AGENTS.md snippet or Codex install | `install-codex`, the shared snippet, `docs/CODEX.md`; verified with real Codex |
| 11 | The instructions and snippet didn't mention the inspection tools | Added (item 7 of the snippet; server instructions) |
| 3, 9, 11 | **Hooks couldn't work against a hosted server** (direct database writes; the server can't know the client's project dir) | The hosted hook bridge (`--remote`, `/api/hooks/*`, hashed `X-Horizon-Project`), covered by the end-to-end test |
| 12 | The hook stored the raw test command (could hold inline code) | The first line only, with quoted strings elided (`command_summary`) |
| 12 | The approval log stored raw question and answer text | Redacted |
| 10, 12 | No public data policy page | `docs/PRIVACY.md`, and `/privacy` |
| 14 | "Share of close calls settled from memory" wasn't reported | `horizon stats` → `decisions` (plus `host_followed_share`) |
| 17 | Stale Docker setup | Rebuilt: extras, non-root, healthcheck, nightly sleep job, secrets from `.env` |
| — | Tests wrote exports into the user's real home directory | The test settings now use a temp dir |
| — | Demo: `tests/test_cache.py` broke collection for the whole suite | Added a `textkit/cache.py` stub |
| 10 | Tracing tool (Langfuse) not integrated | **Open (50):** needs an account |
| 13 (8) | Publishing the repo, benchmarks, write-up | **Owner** (`docs/LAUNCH.md`): public release is hard to reverse; benchmarks cost money |
| 18 | The platform benchmark needs an MCP-capable agent (Claude Code headless, "later") | **Owner** decision (`docs/LAUNCH.md`) |

**Phase 8 decisions (defaults under the decision rule)**
- **Codex tool pre-approval:** `default_tools_approval_mode = "approve"` for Horizon only, the counterpart of Claude Code's `enabledMcpjsonServers`. Human-only actions still ask through MCP user input.
- **Codex `env_vars` passthrough** lists names only (the database URL, team, embedder, scorer, export dir, state secret, scorer keys). No values go into the project's config file.
- **Hosted hooks parse on the user's machine.** Only counts, failing test names, the elided command, commit ids and a hashed project key are sent. Restore commands say `git -C .` (no path). In Claude Code, the redacted 120-character prompt snippet for `/rewind` is still sent (disclosed in the policy).
- **Project identity in hosted mode** = `prj_` + SHA-256(realpath)[:24], set by the installer as an MCP header and recomputed by each hook. It's never a path.
- **Snippet file renamed** `agent.snippet.md` (one text for CLAUDE.md and AGENTS.md). The Claude-only `/rewind` line is phrased for both hosts.
- **Version `1.0.0rc1`.** `v1.0.0` is tagged at launch, after the benchmark and the owner's go-ahead.
- **The README states the repo is private until launch.** Making it public waits for the owner.

**Session 13 decisions (defaults under the decision rule)**
- **The first learned world model is non-parametric (kernel regression over outcome history), not a neural latent model.** It is useful with 10 episodes and grows with every outcome. A Dreamer-style model needs thousands of trajectories, and this is closer to a contextual bandit (state, option, outcome) than a sequential environment. It replaces the kernel model through `HORIZON_WORLD_MODEL_CLASS` once it passes the same gate.
- **Kernel:** weight = exp((similarity − 1) / 0.1) for similarity ≥ 0.5, over episodes whose option matches (`same_option`), shrunk by one pseudo-episode of the team's base rate. Confidence = n / (n + 3).
- **The world model reads archived episodes too:** they are world-model training data (decision 5), even though retrieval skips them.
- **Gate = replay, not the decision log.** Every `record_outcome` makes an episode, while decisions are rarer, so the replay has far more data. It is honest because each prediction uses only earlier episodes. Stand-ins = `jev`, `llm`, `sim` and `host` predictions that weren't low-confidence. The model must win on *every* stand-in with 30+ pairs, by a one-sided paired test at 95 %, so a small lead from luck doesn't count. A forecast with less than one effective episode isn't scored (the model would defer).
- **`auto` is the default.** It's safe: with no data it is pure shadow logging, and it switches on only when the gate says so. The gate result is cached for 10 minutes per process.
- **An active world model settles a close call only when every untested close option has confidence ≥ 0.6.** It goes after memory (real results) and before try-and-rollback and spikes (§6: cheapest first). Jev still re-scores with the forecasts as evidence (§5: "the simulation informs; Jev decides").
- **Forecasts fill a missing estimate dimension only when no option has a host estimate for it and every option has a forecast**, so options are never compared across sources.
- **Attempt usage** = the sum of the transcript's model turns (input, output, cache write and cache read tokens; streamed turns counted once; subagent sidechains excluded) from the last `recall_context` to the `record_outcome` about to run, plus the elapsed time. The host's own values win when given. Cost isn't derived (it needs per-model prices).
- **Hook matcher** `mcp__horizon__recall_context|mcp__horizon__record_outcome` (a regex). Re-running the installer updates existing projects.

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

Every question raised in sessions 0–11, resolved in the session 12 review unless it needs the owner. The full reasoning for earlier resolutions is under "Decisions made".

| # | Question | Resolution |
|---|---|---|
| 1 | The existing memrouter project | Found (`~/Desktop/memrouter`). Its finding, that scoped retrieval beat activation on real data, became the scope knob: task > project > team, and `other_project_factor` (0 = walls). The bounded strength factor avoids its "base-level activation swamps relevance" bug. |
| 2 | The MemRouter name clash | The product is **Horizon**; `memrouter` is only the internal component name. |
| 3 | Memory in Phases 4–5 | Superseded: Phase 6's full recall feeds `evaluate_options`. |
| 4, 5 | Storage split; auth before Phase 7 | Approved defaults (round 4); Phase 7 added team keys. |
| 6–9 | Phase 1 environment, weights, smoke set, pricing | Approved (round 3). |
| 10, 33, 40 | No benchmark of the platform (mini-SWE-agent can't call MCP) | **Built:** `horizon-bench run --agent claude-code [--with-horizon]`, the §18 second baseline, with the same agent and model for both runs. Running it needs the owner (below). |
| 11–13 | Jev fallback, thresholds, high-stakes rule | Built and approved (round 5); values in `PROJECT.md` §5. |
| 14 | Surprise when both values are 0 | Superseded by the zero-centred formula (round 5). |
| 15 | Decay maths, "helpful" | Defined and documented (`MEMROUTER.md` §5 step 3, §8). |
| 16 | Mixed-operator condition match; who says "severe" | Defined (`MEMROUTER.md` §6). The host classifies severity (the tool description lists the cases), and a severe outcome now also escalates at once. |
| 17 | "Heavy testing"; the world-model log format | Defined (`PROJECT.md` §6, §9). |
| 18–26 | Phase 2 items (redaction, hosts, hooks, embeddings, the task list) | Resolved in rounds 4–5 and session 4. |
| 27, 28 | Check litellm/sb-cli fields on the first real run; the spend | **Owner** (real runs). |
| 29, 34, 35 | Pre-existing failures; surprise bias | Resolved (round 5). |
| 30 | Hosts without hooks get no checkpoints | `recall_context(checkpoint_commit=…)`. |
| 31 | Severe outcome: escalate at once? | Yes: a severe outcome escalates to a human immediately (§9). |
| 32 | Did the host restore? | The recall hook compares the tree with the rollback target and refuses one recall (session 4). |
| 36 | Subagent provenance | `record_outcome(subagent=…)` → `agent_id = client:subagent`. |
| 37 | Jev pricing and rate limits | Jev works live; the SDK retries 429/529. Pricing is unpublished: **owner** (TypeSafe account). |
| 38 | The LLM scorer never run for real | Built and tested through the real SDK over a mocked transport. Running it costs API spend: **owner**. |
| 39 | Untuned weights and thresholds | `horizon compare-scorers` produces the data (agreement, Brier against outcomes). The defaults stay until there's data. |
| 41 | Jev rarely produces close calls | Kept the 0.10 margin; low confidence also triggers checks. The decision log's `settled_from_memory_share` and `close_calls` track it. |
| 42 | Possible high-stakes false positive (Celery/Redis) | Accepted: it only matters on a second tie, where a human is asked. |
| 43 | Spike cost | Capped by the per-spike budget, skipped when static checks fail, and reused from memory. |
| 44 | The headline improvement is one constructed scenario | The scope knob gives the owner's alternative design; the real evidence is the Claude Code `--with-horizon` repeated runs. |
| 45 | Predictor trust doesn't affect scoring | A scorer's measured bias is corrected after 20 outcomes; raw predictions are still recorded. |
| 46 | Spike results missing from the recall slice | Added (§6 step 6). |
| 47 | Privacy hard-delete | The mechanism is built: operator-only `horizon purge-memory`, inert until used. The **policy** for legal requests is the owner's. |
| 48 | Pricing, free tier, payments | **Owner.** |
| 49, 51 | Interactive approval UX (Claude Code, Codex) | Not automatable. Every non-interactive path is safe; someone should try it once interactively. |
| 50 | Tracing (Langfuse) | **Owner** (an account). The call log, decision log, weight log and `horizon stats` cover internal debugging. |
| 52 | Codex records its baseline run as an outcome | Acknowledged, not recorded (the baseline-echo guard). |
| 53 | Should `auto` switch the world model on by itself, or should the owner flip it after reading `horizon eval-world-model`? | Default: `auto` (it switches on only after the gate passes, and the scorer still decides). Set `HORIZON_WORLD_MODEL=shadow` to keep it manual. |
| 54 | Codex's transcript format differs, so attempt usage isn't captured there | Default: Claude Code only. Codex outcomes keep host-reported tokens (usually none). |
| 55 | When to build the Dreamer-style model | Default: once there are a few thousand real episodes with outcomes (after the benchmark runs), trained on the exports, and plugged in through `HORIZON_WORLD_MODEL_CLASS` behind the same gate. |

**Needs the owner** (credentials, money or an irreversible decision): 27–28 (real benchmark runs), 37 (Jev pricing and terms), 38 (running the small-LLM scorer), 47 (the erasure policy), 48 (pricing and payments), 50 (tracing), making the repo public, and the hosting provider.

## Next step

1. **Owner:** review `docs/LAUNCH.md`. The launch waits on your go-ahead to make the repo public, API credits for the benchmark, a hosting provider and pricing.
2. Build the Docker image once on a machine with Docker (`docker compose up --build`).
3. Run the Claude Code benchmark with and without Horizon (`horizon-bench run --agent claude-code [--with-horizon]`). This is the first real data for the world model's gate; check it afterwards with `horizon eval-world-model`.
4. Re-run `horizon install-claude-code` in existing projects, so the new `record_outcome` hook is installed.
5. Launch day: merge, tag `v1.0.0`, run and publish the benchmark, go public, deploy.
