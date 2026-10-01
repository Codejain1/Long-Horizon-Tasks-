# Progress

Updated at the end of every session. See `CLAUDE.md` for the working rules and `docs/PROJECT.md` §0 for the build order (v2; §13 is the v1 order).

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

- **Session 12 — review and cleanup pass** (branch `claude/review-pass`, on top of Phase 8; PR #9 was still open).
  - **Open questions:** all 52 resolved or handed to the owner (the table below). New code for 30, 31, 36, 45, 46, 47, 52 and 1/44; the benchmark gap 10/33/40 is now a Claude Code headless agent, and 39 has a scorer comparison tool.
  - **Gap audit against PROJECT.md and MEMROUTER.md, found and fixed:**
    - scope preference now covers task > project > team (MEMROUTER §10); only project was implemented before;
    - weight changes weren't audited (§11), so every strength, stability and link change now goes to `weight_log` with its reason;
    - context tokens per recall weren't measured (§15);
    - spike results weren't in the recall slice (§6 step 6);
    - predictor trust didn't feed scoring (§12);
    - no benchmark could measure the platform itself (§18, §14);
    - the scorer comparison experiment didn't exist (§5).
  - **Security:** a hosted-mode **team-isolation bug** was fixed. Hook data was matched on the hashed project key alone, so two teams with the same project path could mix; it's now scoped per team. Host-supplied ids are capped. A new database-wide **privacy sweep test** pushes code through every host-writable field and finds none stored (SQLite and Postgres). The git history is clean of keys.
  - **Quality:** a dead-code scan found audit data that only the tests could reach, now surfaced in `show_memories` and `horizon stats`. No TODO/FIXME or leftover debug code; the one debug `print` is gated by `HORIZON_DEBUG`.
  - **Docs:** PROJECT.md and MEMROUTER.md now carry "As built" specifications that match the code; README, CLAUDE_CODE, CODEX, HOSTING, LAUNCH and PRIVACY are updated.
  - **Reliability** with the finished platform and live Jev, including the new long multi-step task 7: a full Sonnet run passed all 7 tasks with every workflow metric at 1.0 (`demo/reliability/RESULTS.md`). Two earlier runs were cut short by the Claude Code usage limit, and `report.py` now marks those `aborted`. One hook was cancelled at its 10 s timeout under machine load, so the installer now uses 30 s.
  - **Tests:** 427 pass on SQLite and Postgres 14 + pgvector.

- **Session 13 — long-horizon A/B** (branch `claude/longhorizon-eval`).
  - **Built:** `evals/longhorizon/`, 2 scenarios × 4 fresh Claude Code sessions each, with hidden acceptance tests and constraint checks run outside the repo. Reference solutions prove the tests are fair; a naive store proves the concurrency test discriminates. 5 tests in `tests/test_longhorizon.py`.
  - **Run 1** (Sonnet, live Jev; `evals/longhorizon/RESULTS.md`): **both arms were perfect** on the hidden tests and constraints. **Horizon cost +33 % (ledger) and +140 % (notes).** The baseline kept its own `NOTES.md`.
  - **Team scenario** (3 projects, fresh repos, the team's rules stated once): without Horizon, project 2 lost the rules (dashed ids, `+00:00` stamps). With Horizon it kept the timestamp rule, only because a past episode's situation matched, and lost the id rule. So **`start_task(team_rules=…)`**: the rules the user sets for every project are shown at session start in any project of the team. Run 1 was cut short in project 3 by the usage limit. **Run 2, with team rules:** projects 2–3 got 8/8 with Horizon against 5/8 without, at +39 % cost (`evals/longhorizon/RESULTS.md`).
  - **Lean profile** (`install-claude-code --profile lean`): continuity only, meaning `start_task` with constraints and team rules, one `record_outcome`, `evaluate_options` only for hard-to-reverse choices, no baseline run and no per-run nudges. **Ablation, 3 repeats:** projects 2–3 got 24/24 with lean, 24/24 with full, and 15/24 without Horizon. Cost was +55 % for lean and +78 % for full.
  - **Hook-based lean** (session 14): the agent makes no Horizon calls. `UserPromptSubmit` captures the task and rule sentences, a new session completes the last one, the lean server leaves out `start_task`/`recall_context`/`record_outcome`, and session start lists **recurring pitfalls**: checks that failed in 2 or more earlier sessions. Team ablation, 3 repeats: 24/24 against 15/24, at +40 % cost, mostly from doing the rules' extra work. Also fixed `redact` pairing the wrong backticks.
  - **Backlog scenario** (unattended, 8 tickets, CI policy checks): both arms 13/13; sessions with a failed test run, 6/8 without Horizon against 2/8 with lean, at +13 % cost. The pitfall warning never fired, so the gain isn't attributable to it yet.
  - **Pitfall warning, tested directly** (backlog tickets 4–8 replayed from the no-Horizon state, lean seeded with the real test history): 0 of 5 sessions broke the recurring changelog policy with the warning, against 3 of 5 without, at **17 % lower cost**. The first result where Horizon is both better and cheaper. It also found that CI through `./ci.sh` was never captured (fixed).
  - **Jev picks out rules** (two yes/no questions per sentence, one request): 97 % against keywords' 62 % on 32 labelled sentences (`evals/rules/`). Keywords remain the fallback.
  - **Hosted lean** (`--hosted URL --profile lean`, hooks only): team rules stated by one person reach teammates' sessions on other machines and projects. Tested over real HTTP with two keys on one team, and a second team that never sees them.
  - **Gap found and fixed:** every Horizon session finished its own task, so session 1's constraints never reached later sessions. SessionStart now lists the project's recently completed tasks and their constraints.

- **Session 15 — v2 Phase 0: foundation** (copy `~/Desktop/Long-Horizon-Tasks-v2`, branch `v2/experience-architecture`; the owner asked for v2 work to happen in a copy, leaving the original folder untouched).
  - **Direction:** `PROJECT.md` §0 now holds the v2 architecture (the experience layer: principles, layers, build order phases 0–5). `MEMROUTER.md` carries a v2 note, and §13 is marked as the v1 record.
  - **Built:**
    - `src/horizon/eventlog/`: a generic, versioned `Event` schema (`kind`, `text`, `action`, `result`, `usage`, `data`; nothing coding-specific), and an `EventStore` (new `events` table, SQLite and Postgres) that redacts every field again on append;
    - `eventlog/transitions.py`: a pure `replay(events, adapter)` giving the state after every event, plus a (before, action, result, after) transition per step, the world model's future training data;
    - `src/horizon/adapters/`: the `DomainAdapter` interface (state, reduce, risk classification, outcome, situation) and `CodingAdapter`. It turns Claude Code hook payloads into events (test runs with real counts, bash, edits, writes, MCP and other tools; reads are skipped), with rules for risky commands and paths, and each session's outcome from its test runs;
    - `taskstate/usage.turn_usage`: each turn's real tokens and latency from the transcript (streamed turns counted once, sidechains skipped). `attempt_usage` is unchanged;
    - lean hooks log every session locally: PostToolUse now also covers edits (lean only), and two new lean-only local hooks, `PreCompact` and `SessionEnd`. Logging can't change a hook's output or break it;
    - `docs/EVALS.md`: the evaluation gate rules and specs for `marathon`, `compaction` and `stall`, plus the proposed `observe` profile.
  - **Acceptance criteria**, all met:
    1. the generic, versioned schema round-trips on both backends;
    2. the privacy sweep covers the event log (code in every hook field, none stored);
    3. all six hook types map to events; irrelevant or malformed payloads are safe;
    4. replay is deterministic and pure, and gives correct state;
    5. the risk classifier is 100 % on the labelled set;
    6. the session outcome is derived from events;
    7. logging doesn't change hook output, and a failing log never breaks a hook;
    8. the installer adds the new hooks only for local lean (full and hosted are unchanged);
    9. turn usage dedupes streamed turns and skips sidechains;
    10. the evaluation specs are written;
    11. the full suite passes on both backends;
    12. hook latency stays at 0.21–0.22 s warm, the same as before (0.16 s of it is imports).
  - **Mutation checks:** injected bugs in the coding state reducer (failed runs not counted), a risk rule (`npm publish` missed) and redaction (store-side `sanitise` disabled) were each caught by a test.
  - **Tests:** 492 pass on SQLite and Postgres 16 + pgvector (471 before, plus 21 new ones in `tests/test_eventlog.py` and `tests/test_privacy.py`).

- **Session 16: v2 Phase 1, track E** (the owner chose "track E first"; commits 20ff318, 1f2d316, c44afa9, 609bfca and the docs commit). Built, not yet run with real Claude Code sessions.
  - **E1, the `observe` profile:**
    - `install-claude-code --profile observe` installs lean's hooks with no MCP entry and no CLAUDE.md block, and removes both if present. `--hosted` is refused.
    - The hooks log events but return nothing and keep no task state.
    - Every hook call's first event records `injected_chars`, the characters of its output the model reads.
  - **E2, harness metrics** (`evals/longhorizon/run.py`, `report.py`):
    - an `observe` arm;
    - failing checks from the agent's own test runs;
    - repeat failures, and regressions against the previous session's hidden results (both also across `--resume`);
    - per session, Horizon's events by kind and the characters it injected;
    - `events.jsonl`, the run's whole event log.
  - **E3, `marathon`:**
    - The starting codebase is an invoicing service of about 400 lines. It has 5 CI policies, CI runs in `Asia/Kolkata`, and a conftest runs the tests in reverse order.
    - There are 24 tickets, and the cents and `uuid4().hex` rule is stated only in ticket 1.
    - 40 hidden ticket tests plus 5 policy tests.
    - A reference solution passes all of them and the repo's own CI.
    - New drift checks: `no_floats` and `uuid_hex_ids`.
  - **E4, `compaction`:**
    - One session can now be several resumed invocations (`--resume <session id>`). Usage is summed, the logs are joined, each invocation is recorded and compactions are counted.
    - The `tab` scenario gives the goal, 4 constraints and parts 1–3, then sends `/compact`, then gives parts 4–8 with a shortcut in each part.
    - New `utc_only` check.
  - **E5, `stall`:**
    - Six tickets on the marathon codebase (the new `start_from` field, plus an overlay).
    - #3 asks for output that a platform-owned silence policy forbids. #5 needs a binary only CI hosts have. The four controls reuse marathon tickets.
    - Each session records whether it wrote `BLOCKED.md` and whether the note gives the right reason.
    - Hidden checks catch faking.
    - `--seed RUN_DIR` replays another run's sessions through the installed hooks.
  - **Acceptance criteria**, all met except the F1 check, which needs a real session:
    1. observe logs the same events as lean, prints nothing, leaves task state unchanged, and installs as hooks only (switching back to lean works);
    2. `injected_chars` is 0 under observe and positive under lean;
    3. repeat failures and regressions come from the transcript and the hidden tests, not the agent's summary;
    4. each run exports its event log;
    5. every new scenario's reference passes all hidden checks, and marathon's reference also passes its repo CI;
    6. each planted trap, shortcut and fake is caught by its own check and by nothing else. That covers 5 policies, 2 quirks, 5 drift cases, the storage and backup traps, 6 compaction shortcuts and 4 stall fakes, while an honest `sign_invoice` is not flagged;
    7. a compaction session resumes one Claude session (verified against a fake `claude` that records its arguments);
    8. `BLOCKED.md` is scored per session: a right reason, a wrong one, and a note on a solvable ticket are told apart;
    9. seeding replays full sessions through the hooks;
    10. **not verifiable without a real run:** that `claude -p --resume <id> "/compact"` really compacts, which hooks fire, and the stream's compaction marker (question 62).
  - **Mutation checks:** these deliberately injected bugs were each caught by a test, and the code was then restored:
    - the observe handler speaking;
    - the repeat-failure count;
    - dropping `--resume`;
    - accepting any `BLOCKED.md` reason;
    - seeding without tool calls.
  - **Tests:** 537 pass on SQLite and Postgres 16 + pgvector: 492 before, plus 45 new (`test_marathon.py` 19, `test_compaction.py` 9, `test_stall.py` 10, and 7 in `test_longhorizon.py` and `test_eventlog.py`).

- **Session 17: v2 Phase 1, track A: session episodes from the event log.**
  - **Built:**
    - `src/horizon/eventlog/episodes.py`: `draft_episode` (pure) turns one session segment's events into the arguments of `MemRouter.record`. `SessionEpisodes` finds ended segments with nothing recorded, writes them and marks each one written or skipped in the new `session_episodes` table.
    - The lean `SessionEnd` hook writes the project's pending segments after its own work, isolated from it.
    - New `horizon episodes-from-log [--project DIR]` backfills any log.
    - `TaskStore.session_task` scopes each episode to the lean task its session worked on.
    - The coding outcome now carries the last run's counts (`facts["tests"]`).
  - **Acceptance criteria**, all met:
    1. a lean session with tests becomes one episode: the first prompt, the last run's pass rate and counts, the turn's real tokens, low confidence, and scoped to its project and task;
    2. every segment is written once, and a resumed session adds an episode for its new segment only;
    3. sessions with no test run or no prompt are skipped with the reason and not reconsidered;
    4. a failed write leaves the segment pending, and it's caught up at the next session end;
    5. a failing memory or writer never changes or breaks the hook;
    6. only lean writes session episodes (observe, full and hosted don't);
    7. the episodes feed memory: the next similar session's surprise is measured against their history, memory's forecast is scored under task type `session`, and recall finds them;
    8. code in a prompt never reaches an episode;
    9. the CLI backfills an observe log;
    10. both backends pass. The hook takes 0.7–1.0 s at session end when it writes an episode with fastembed, and 0.22 s when nothing is pending, the same as the other hooks. Episodes carry the server's embedding model name, so the MCP server finds them.
  - **Mutation checks:** turning off the dedupe, starting every segment at the session's start, writing for every profile, and marking failed writes as done were each caught.
  - **Also fixed:** track E's docs commit changed `docs/PRIVACY.md` without the packaged copy that `/privacy` serves, which `test_phase7` caught. Both copies now match, with a line on session episodes.
  - **Tests:** 561 pass on SQLite and Postgres (537 before, plus 24 in `tests/test_session_episodes.py`).

- **Session 18: Q66 (idle sessions), Q65 (past sessions as context), track B (Jev as outcome judge).**
  - **Owner's direction:** good context should help the agent build better and cut the cost of trial and error ("try this, if it doesn't work try another"). Pick the best answer for Q66. The TypeSafe key was provided, which enables track B.
  - **Built:**
    1. **Q66, idle sessions:** `SessionEpisodes.pending` also closes a session whose last event is older than `session_idle_minutes` (120) and isn't a `session_end`.
       - If the session carries on later, the rest becomes a new segment: `after_n` is the later of the previous `session_end` and the previous recorded segment.
       - Lean's `SessionStart` runs the catch-up before logging its own start, so a resumed session's idle part still reads as idle. It loads the embedder only if something needs writing.
    2. **Q65, past sessions as context:** `eventlog/briefing.py`, behind `HORIZON_SESSION_MEMORY` (default `off`).
       - A lean task's first message recalls similar session episodes in the project, plus lessons and fears.
       - For each one, the episode's event-log segment gives how it ended, how many test runs failed on the way, the files changed, the tests that failed on the way, and those still failing.
       - Only sessions with similarity ≥ 0.8 are included (`session_memory_min_similarity`). Identical outcomes merge into one line with a count. The note is capped at 1,200 characters and uses no code.
       - The evals get a `candidate` arm (lean plus `HORIZON_SESSION_MEMORY=on`).
    3. **Track B, the outcome judge:** `eventlog/judge.py`. With `HORIZON_SCORER` set to jev or llm, a segment with no test run but some evidence (an edit or a follow-up message) is judged.
       - Jev puts it on five ordered levels, reading the redacted prompts and activity counts, and asks per follow-up whether the message is a correction.
       - `actual.success` is the level's position, with signal `implicit`.
       - There's no episode when confidence is below 0.5, or when Jev says "done" but the last message is a correction.
       - A judge failure is recorded as a skip, so there's no retry storm. `episodes-from-log --rejudge` reconsiders earlier "no ground truth" skips.
       - The same scorer now filters lean's recalled memories for relevance (the attention filter), as it already did on the MCP server.
  - **Acceptance criteria:** all met.
    1. A crashed session becomes an episode once idle, and never while it's still running.
    2. A session quiet for less than the timeout stays open, and the timeout is configurable.
    3. A session resumed after going idle splits where it went quiet.
    4. An idle segment with no ground truth is skipped once.
    5. Session memory is off by default.
    6. With it on, a similar task starts with where the last one went and what tripped it up, and a failed session shows what was still failing.
    7. An unrelated task, or another project, gets nothing.
    8. With no history, the embedder isn't loaded.
    9. Only a task's first message gets the note.
    10. The note stays within budget however much memory holds, and identical sessions merge into one line.
    11. A memory failure never stops a task starting, and no code reaches the note.
    12. In the `candidate` arm, session 2 gets the note and `injected_chars` rises; `lean` is unchanged.
    13. A session without tests gets a judged `implicit` outcome from counts and redacted prompts, never paths.
    14. Tests override the judge.
    15. With nothing to judge, Jev isn't called.
    16. An unsure or contradicted verdict isn't stored.
    17. A failing judge never breaks the hook.
    18. With no configured scorer, nothing is judged.
    19. One scorer serves a whole batch.
    20. `--rejudge` works.
  - **Live checks** (key passed only as `TYPESAFE_API_KEY`, never written to a file):
    - Jev on three cases: user confirmed 1.00 (confidence 0.99), still broken 0.01 (0.98), corrected then accepted 0.91 (0.70). Each call took 0.3–0.6 s.
    - End to end through the real hooks with `HORIZON_SCORER=jev`: a session with no tests became an `implicit` episode with success 0.998. Session end took 0.61 s.
  - **Latency** (fastembed, separate processes, like Claude Code):
    - The first prompt of a task takes 0.72 s with session memory on and history present, and 0.21 s with it off.
  - **Mutation checks:** each of these deliberately injected bugs was caught by a test, and the code was restored:
    - `after_n` ignoring recorded segments;
    - no catch-up at session start;
    - no relevance floor;
    - the note on every prompt.
  - **First real run (owner-approved, question 67):** marathon tickets 1–8, `lean` against `candidate`. `candidate` hit the usage limit in ticket 7, so tickets 1–6 were compared.
    - Quality was equal, but `candidate` cost more: +29 % turns, +44 % tokens, +33 % cost.
    - Every earlier ticket had passed cleanly, so the note held nothing to avoid, only file lists.
    - Changes made as a result:
      - the note shows only past sessions that hit trouble;
      - a past task's label drops the preamble it shares with the new goal (every marathon prompt opens the same way).
    - `HORIZON_SESSION_MEMORY` stays off.
  - **Also fixed:** a clock-dependent track E test. From 18:30 to midnight UTC, the "India's days" compaction shortcut is also (rightly) caught by the "today is the UTC date" check. The test now expects that during those hours.
  - **Tests:** 616 pass on SQLite and Postgres (561 before, plus 55):
    - `test_session_briefing.py`: 13;
    - `test_outcome_judge.py`: 10;
    - `test_session_episodes.py`: 4 more;
    - `test_longhorizon.py`: 1 more.
    - The `log` fixture moved to `conftest.py`.
- **Session 19: track C, world model v2 (session forecasts with Jev as the prior).** C came before D because stall detection (Phase 2), and the composer's "when to show", both need a forecast. It also serves the owner's goal of cutting trial and error, and it can be evaluated offline for free.
  - **`eventlog/forecast.py`** (model `session-v2`): `SessionWorldModel.predict(team, project, task, so_far=None)` returns a `SessionForecast`:
    - the chance the session ends with its tests passing;
    - failing test runs on the way (mean and p90);
    - tokens (median and p90) and turns;
    - confidence, effective samples, and which prior was used.
  - **How it forecasts:**
    - It uses a similarity kernel over the team's session episodes, the same as `kernel-v1`: width 0.1, a similarity floor, and the `other_project_factor` walls.
    - Each episode's failing runs, runs and turns come from its segment's events.
    - The estimate is shrunk towards a prior. That prior is the project's base rate, blended with Jev's reading of the task (5 ordered levels for failing runs, worth 2 sessions) by how many sessions the project has.
    - `so_far={"failed_runs": k}` counts only sessions that also hit k or more failing runs. This is stall detection's question, and Jev isn't asked it.
  - **`horizon eval-forecast [--jev] [--limit]`:** replays each session from strictly earlier ones. It reports:
    - the Brier score against memory's current surprise baseline and the base rate, with a paired one-sided test for each;
    - the mean error on failing runs;
    - the mean absolute log error on tokens.
    - With `--jev` it also scores the prior alone.
  - **Acceptance criteria**, each covered by a test in `tests/test_forecast.py` (10 tests, run on SQLite and Postgres):
    1. With no history, it gives the fallback with confidence 0.
    2. Similar sessions drive the forecast: easy stays easy and hard stays hard, with the right p90s, tokens and turns.
    3. An unrelated task gets the prior.
    4. Walls between projects hold.
    5. The Jev prior leads with nothing to go on, and similar sessions outweigh it.
    6. A failing scorer falls back to the base rate.
    7. `so_far` filters correctly.
    8. Replay forecasts only from the past and beats the base rate on separable data.
    9. Replay needs 2 sessions.
    10. The CLI works.
  - **Real data** (the Q67 marathon logs: lean 8 sessions, candidate 6):
    - The pipeline runs end to end with fastembed.
    - Every session ended green, so the constant baselines score 0 and there's nothing to beat.
    - This exposed a flaw, which is now fixed. Jev alone reads a task as risky: Brier 0.28, predicting 2+ failing runs for tickets that passed first time. At a fixed weight of 2, it dragged the model to 0.12. With the project blend, the model's Brier score is 0.023.
  - **Latency:** about 20 ms per forecast over the real logs, once the embedder is loaded. The first load of one script hung once and never again (question 71).
  - **Tests:** 636 pass on SQLite and Postgres (616 before, plus 20). Ruff is clean on the changed files.
- **Session 20: track D, the context composer** (`src/horizon/composer.py`, behind `HORIZON_CONTEXT=composer`; classic by default).
  - **What the real run showed first.** Lean injects about 300 tokens per session against 0.5–1.9M spent, so the composer's job is value, not trimming. Classic's block also had two defects on the Q67 marathon data:
    - every recent-task line was mostly the shared backlog preamble, cut at 200 characters;
    - constraints travelled only with the last three tasks, so ticket 1's backlog rule (integer cents, `uuid4().hex` ids) was gone from ticket 5 on. That rule is exactly what marathon's drift check measures.
  - **Session start**, composed within `HORIZON_CONTEXT_BUDGET_CHARS` (4,000), kept in this order:
    1. the task being resumed (its goal up to 400 characters, preamble cut);
    2. team rules;
    3. every constraint the project's tasks set (`TaskStore.project_constraints`, newest first, deduplicated);
    4. recurring failing checks;
    5. the last three tasks, labelled without the preamble all recent goals share (cut at its last sentence), at most 120 characters.
    - Each line appears once, in its most essential section. A line that doesn't fit is dropped whole, and so is everything in less essential sections.
  - **First prompt:** with session memory on, the past-sessions note is shown only when track C's forecast expects at least `HORIZON_CONTEXT_MIN_FAILED_RUNS` (0.5) failing runs. One embedder load serves both the forecast and the note, and a failing forecast never stops the task starting.
  - **Wiring:**
    - local lean (`run_hook`) and hosted lean (`create_web_app(context_budget=…)` from `http_app`);
    - the evals' `candidate` arm now sets `HORIZON_CONTEXT=composer` as well as session memory;
    - `run.py --set KEY=VALUE` for ablations;
    - `results.json` records each arm's settings;
    - `distinct` moved to the composer, which the briefing imports.
  - **Acceptance criteria**, all met and tested (`tests/test_composer.py`, 13 tests; 2 more in `test_longhorizon.py`):
    1. `classic` is the default, and classic output is unchanged (every earlier test passes as it was).
    2. Ticket 1's rule reaches ticket 11 with the composer, and doesn't with classic.
    3. Labels cut only what every goal shares.
    4. Each line appears once.
    5. A tight budget drops the least essential kinds first, whole lines only, with no empty headings and never over budget.
    6. The budget setting bounds the block.
    7. A resumed session sees its task first.
    8. One troubled session among clean ones is treated as a fluke (no note), and repeated trouble is shown.
    9. One embedder load serves the forecast and the note.
    10. A failing forecast never stops the task starting.
    11. Hosted lean follows the server's setting.
    12. The candidate arm's settings are recorded, and `--set` works without leaking into later runs.
  - **Real data** (the Q67 lean log, ticket 9's session start):
    - 918 characters against classic's 993 (8 % fewer);
    - carries ticket 1's rule, which classic had lost;
    - each recent line names its ticket's feature;
    - built in 0.7 ms.
    - The forecast gate would have kept the past-sessions note out (every similar session was clean). That note was behind the Q67 candidate's +44 % tokens. The forecast adds about 150 ms.
  - **Tests:** 660 pass on SQLite and Postgres (636 before, plus 24). Ruff's one finding, in `web.py`, predates this change.

- **Session 21: Phase 2 (joining): re-anchoring after compaction, stall detection, surprise against the forecast.**
  - **Re-anchoring** (the composer, `HORIZON_CONTEXT=composer`): a `compact` session start restates this session's task in full (up to 1,500 characters; classic shows 200), with where it got to. That means:
    - the user's later messages (up to 5; slash commands such as `/compact` left out);
    - the last test run, with what still fails;
    - the files changed, all from the task and the event log.
    - Then the constraints, team rules and the rest, within the 4,000-character budget.
    - A resumed session keeps its conversation, so it isn't restated. The hosted server gets the start's `source` too.
  - **Stall detection** (`src/horizon/stall.py`, `HORIZON_STALL=on`; the evals' candidate arm turns it on):
    - after each captured test run, lean checks whether the same check failed in each of the last N runs (`TaskStore.session_runs`) with no more tests passing than when the streak began;
    - N is the larger of `HORIZON_STALL_MIN_RUNS` (4) and the forecast's 90th percentile of failing runs for similar sessions, plus 2;
    - it stays quiet when similar sessions that got as far (the forecast's `so_far`) ended green at least 80 % of the time, over at least 2 sessions;
    - it warns once per session. The warning is flagged on the logged event (`stall_warning`), and `run.py` counts it per session (`horizon.stall_warnings`);
    - the embedder and forecast load only once the database-only pattern holds;
    - hosted installs have none yet;
    - `./ci.sh` output is parsed with failing ids, so the stall scenario's runs are seen.
  - **Surprise against the forecast** (`HORIZON_SESSION_SURPRISE=auto` by default, or `forecast`/`fallback`): `SessionEpisodes.write(predictor=…)` records a session episode with the forecast as `predicted`, with the new prediction source `world_model` (an added literal; stored data still validates). With `auto`, that happens only once `gate()` passes: at least `HORIZON_WORLD_MODEL_MIN_PAIRS` (30) pairs, and a clear win over the fallback on the latest 200 sessions' replay. Below 31 sessions the replay isn't even run. The gate takes 59 ms at 200 sessions. A forecast with confidence 0 is no prediction (memory's low-confidence rule), and a failing forecast never stops the write.
  - **Acceptance criteria**, all met and tested:
    - **Re-anchoring** (3 tests plus the hosted case in `test_composer.py`): the task is restated whole with its progress; only a compaction restates it (not a resume, not classic); it stays within the budget however long the session; hosted follows the setting.
    - **Stall detection** (`test_stall_detection.py`, 12 tests):
      - off by default;
      - a stuck session is warned exactly once, with the flag logged;
      - ten failing runs that make progress are never warned;
      - a history of hard sessions raises the bar to 7;
      - it stays quiet when similar long sessions ended green (checked by mutation: with that rule off, the same history warns);
      - the embedder loads only at the 4th stuck run;
      - a failing forecast still warns at the floor;
      - hosted has no check.
    - **Surprise** (`test_session_surprise.py`, 7 tests):
      - `auto` keeps the fallback below the gate, with no replay;
      - `forecast` records the forecast, so an expected outcome has surprise 0;
      - accuracy is tracked as source `world_model`;
      - the gate needs enough pairs and a clear win;
      - `auto` switches once the gate passes;
      - a failing forecast never blocks the episode;
      - `fallback` never builds a forecast.
  - **Tests:** 699 pass on SQLite and Postgres (660 before, plus 39). Ruff is clean on the changed files.
- **v2 session 22: Phase 3 gate runs** (owner-approved, real Claude Code on the subscription, about $25 cost-equivalent in total; full tables in `evals/longhorizon/RESULTS.md`):
  - **F1 verified (question 62):** `/compact` on a resumed headless session compacts it (one `compact_boundary`), and Horizon logs a `compact` session start.
  - **Compaction, 3 repeats of `baseline`/`lean`/`candidate`:** violations after compaction were 2/0/0 for baseline, and zero in every lean and candidate run. Candidate cost ≈ 560 tokens per compaction. It meets the gate's letter but shows no gain over lean. Part 6 (India's "today" against the UTC rule) failed in all 9 runs.
  - **Stall, `baseline` against `candidate`, seeded:** both arms stopped correctly on both stall tickets, with no false alarms and all controls green. But no session had a single failing test run on a stall ticket, so the detector never had anything to see. Candidate's ticket 5 explored for 1.27 M tokens against baseline's 322 k. **Gate not met.**
  - **Marathon, `lean` against `candidate`, tickets 1–12:** both arms hit the usage limit (lean in ticket 7, candidate in ticket 11). On tickets 1–6 both were clean (17/17, 0 violations), and candidate used 10 % fewer tokens, which is within noise. **No signal either way.**
  - **Two bugs the runs exposed, fixed** (`7991b16`): the harness scored a later ticket's correct `BLOCKED.md` reason as wrong once the file passed 2,000 characters; and `split_rules` split after a list number ("1.") and dropped numbered items with no rule keyword under a "these constraints hold:" lead-in.
  - **Tests:** 701 pass on SQLite and Postgres.
- **v2 session 23: refused-command memory** (`src/horizon/deadends.py`, `HORIZON_DEAD_ENDS=on`, off by default; on in `candidate`). Chosen from the Phase 3 transcripts:
  - **The finding.** 8 of 54 real sessions had calls refused by the permissions. The waste was retrying them: five tries of the missing `finsign` (stall ticket 5, 1.27 M tokens). In 4 marathon and stall sessions, in both arms, the same dead end appeared: a `python -c` with a `#` comment, a script in /tmp, a `PYTHONPATH=` prefix, a `cp` from /tmp. Each session rediscovered the same way out (write the script in the repo, run plain `python3`). Refused calls never reach `PostToolUse`, so Horizon had never seen any of this.
  - **What it does:**
    - Refusals are read from the transcript (the `stream-json` log has the same format) and logged once each as `refused` steps. Only a key is stored: the command's first word with any `NAME=` prefix and without an interpreter's version, or `outside` / `hash`.
    - Lean now hooks `PreToolUse` for `Bash`. A command matching a key refused twice this session gets one warning (about 0.2 s per call).
    - Session start lists keys refused twice in earlier sessions (classic and composer, within the budget).
    - Refusals are logged at every turn's end whatever the setting (also by `observe`).
    - The warning is a `dead_end_warning` step: counted for injected characters, and excluded from transitions (`HORIZON_ACTIONS`).
  - **Checked offline on the real transcripts:**
    - the warning comes before the third `finsign` try and in the marathon loops (4 warnings, 1 on a command that would have run);
    - seeded with the question 67 lean run, a new session is shown exactly that dead end (`PYTHONPATH=… python`, `#` in `python -c`, paths outside the project), in about 330 characters.
  - **Harness:**
    - `refused_calls` per session, from the stream, in every arm;
    - `horizon.dead_end_warnings`;
    - seeded history carries refusals.
  - **Tests:** 719 pass on SQLite and Postgres (18 new in `test_dead_ends.py`). Ruff is clean on the new files.
  - **Real check queued** (the usage limit resets at 6:20 pm): `stall` `candidate` seeded as before, then `marathon` `candidate` tickets 1–8, into `/tmp/lh-de`.

- **v2 session 24: Cursor as a second agent (owner: "use your own model for these tests").**
  - **Cursor host** (`src/horizon/cursor.py`, `horizon install-cursor`, `horizon hook <cursorEvent> --host cursor`): lean and observe through `.cursor/hooks.json`. Shell maps to Bash, failing commands are read from `postToolUseFailure`, and prompts come from Cursor's transcript at the next tool call, under a per-session lock. Lean's context no longer mentions `evaluate_options` on Cursor, which has no Horizon MCP tools.
  - **Harness:** `--agent cursor --model composer-2.5` drives `agent -p … --force --trust --sandbox enabled`, with the stream converted to Claude Code's shape.
  - **Real run** (marathon, 3 arms in parallel, `RESULTS.md`): baseline finished 24/24 with 0 hidden failures. On tickets 1–15, lean and candidate each carried one hidden failure from ticket 4 onward, which looks like single-run noise. Tokens were −8 % (lean) and −4 % (candidate), with 0 violations and 0 refused calls in every arm. lean and candidate were cut by Cursor backend disconnects (tickets 22 and 16).
  - **Refused-command memory, real check (Claude Code, `RESULTS.md`):** refused calls fell on stall from 13 to 8 and on marathon tickets 1–6 from 8 to 2. The warning fired at the third `finsign` try and the agent stopped. Tokens didn't fall (stall ticket 5 went from 1.27 M to 1.53 M), so it's not a gate pass. It stays off by default.
  - **Ticket 4 repeated** (3 runs per arm): baseline misses the same hidden test, so the gap is noise (question 82).
  - **Harness retries dropped sessions:** a session with no result (the agent's backend dropped, or it hung past the timeout, 25 minutes on Cursor) is rerun from the last commit, up to 2 times, and marked `retries`. Found while testing: an error result with no session id wasn't counted as aborted.
  - **Repeated runs, baseline against lean** (marathon tickets 1–12, 3 runs each, Cursor): hidden failures were equal (1.33 each), tokens were equal (2.90 M against 2.88 M), and violations were 1 against 0 (not evidence). **Lean took 21 % longer** (0.6 s per tool call, one Python process per hook).
  - **Hook latency fixed:** lazy numpy, deferred model builds, and Cursor hooks only on shell and edits with no `preToolUse`. Lean's 12 tickets take 16.1 min against 20.3 before and baseline's 16.7.
  - **Lean session start:** no wording that asks for tool calls, goals without the shared preamble, and **every project constraint** listed instead of only the last 3 tasks'. Handoff showed a captured rule dropped before the ticket that needed it.
  - **New scenario `handoff`** (8 tickets): rules from tickets 1–2 are needed in tickets 6–8. Reference and naive solutions are proven by a harness test. First runs (3 per arm, Cursor): rules survive exactly when the agent wrote them into the README in ticket 1. Candidate passed the fines cap and dates in 3/3 runs, but it also wrote the README every time. Masking failed nearly everywhere: only Jev captures that rule, and Jev was blocked.
  - **Cursor hides secrets from hooks:** environment variables that look like secrets reach hooks as `...`, so `TYPESAFE_API_KEY` can't reach Horizon's hooks under Cursor (question 85).
  - **Jev under Cursor:** the key is read from the macOS Keychain (`horizon-typesafe`) when the environment variable is missing or hidden (question 85).
  - **Contamination found and fixed.**
    - **Found:** Cursor's agent reads anything on disk. It followed the Python path in `.cursor/hooks.json` into this repo (hidden tests, reference solutions) and globbed the run folder (`results.json`). Sessions doing so in the first handoff runs: 1 to 7 of 24 per arm. Those results and the earlier Cursor results are marked in `RESULTS.md`.
    - **Fixed:** with the real CLIs (or `--isolate`), each repo and database sits alone in a `lhx-` temp folder, linked from the run folder and copied back at the end. The agent's Horizon is a non-editable install cached in `~/.cache/horizon-eval/`. The agent's environment drops anything naming this repo.
    - **Measured:** every session records `outside_reads` (eval / other runs / agent memory / elsewhere).
  - **Handoff, isolated (3 runs per arm, Cursor):** 0 of 96 sessions read the eval tree or another run.
    - **Lean + Jev and candidate + Jev passed all 4 rule checks in every run (24 of 24).** Five of those six runs wrote nothing about the rules into the repo.
    - **Baseline passed 2 of 12**, only in the run that wrote the rules into the README.
    - **Keyword lean passed 6 of 12** (fines and dates, the rules it wrote into the repo itself). It passed no masking check, a rule only Jev captures.
    - Jev arms' tokens were equal to or below baseline's (−4 %, −8 %), with 9 % more wall time.
  - **New scenario `rental`** (12 tickets, an Indian no-brokerage rental platform with deposits placed with a lending partner): nine business and legal rules from tickets 1–3 are needed in tickets 5–12. A harness test proves it: the reference passes all 23 hidden tests, and a version that forgets the rules fails exactly the 9 rule tests.
  - **Read guard:** path isolation wasn't enough. Keyword-lean agents searched the home folder and read the hidden tests (first rental batch, discarded).
    - Every agent now runs under a macOS Seatbelt profile nested inside Cursor's sandbox.
    - It denies reads of this repo, the run folders, other runs' temp folders and every agent project folder except the run's own. The owner's own Cursor chats are included, since they name the hidden tests.
    - It was verified with the real Cursor CLI.
  - **Rental, guarded (3 runs per arm, Cursor):**
    - Lean + Jev passed 22 of 27 rule checks; baseline 8, keyword lean 14 (p ≈ 0.0001 and 0.02).
    - Lean + Jev used 2.58 M tokens per run against baseline's 4.57 M, and 18.0 min against 20.0.
    - Jev's misses come from over-capture: 43–46 stored constraints, only the 20 newest shown, so the phone rule from ticket 2 had dropped off by ticket 11 (question 88).
  - **Over-capture fixed:** lean's session start leaves out sentences that the latest message repeated from an earlier one, and shows up to 40 constraints. Rental rerun, lean + Jev: **26 of 27 rule checks** (was 22; baseline 8), 2.85 M tokens, 14.5 min, 0 suspect sessions.
  - **Tests:** 737 (490 pass on SQLite; the 247 Postgres variants were skipped this time).

## In progress

- Nothing running.

## Decisions made

**v2 session 24**
- **Cursor runs use `--sandbox enabled`.** An unsandboxed probe ran `pip install --break-system-packages` into the system Python.
- **Cursor gets lean and observe only.** The full profile needs the MCP server, and headless Cursor fires no prompt or stop hooks, so prompts are caught up from the transcript.
- **No retry after a backend disconnect.** The harness stops the arm, as it does for a usage limit. Retrying a half-done ticket would mix two attempts.
- **Isolation is on by default only for the bare `claude` / `agent` commands.** Tests pass fakes by path and keep the old layout; `--isolate` / `--no-isolate` override.
- **The agent's Horizon env is cached by a hash of `src/` and `pyproject.toml`,** with extras `embeddings` and `decision`, and `direct_url.json` deleted (it records the source path). A file lock lets parallel runs build it once.
- **Outside reads come from tool-call arguments**, from the raw Cursor stream (which keeps reads, globs and searches) or Claude's `tool_use` inputs. The agent's own transcripts count as `agent_memory`: they're the agent's built-in memory, the same in every arm. The repo's own temp folder and the agent's Python count as inside.
- **Seatbelt read guard on macOS** (`sandbox-exec`, nested over Cursor's sandbox). The run's own repo, database folder and agent project folder stay readable. Writes are left to the agent's own sandbox. It's off on other platforms, where `outside_reads` still measures attempts.
- **Contaminated runs are replaced, not filtered:** the first rental batch is discarded, and batch 2's baseline r1 is replaced by r4.
- **Earlier Cursor results are kept, with a caveat,** rather than deleted: the marathon runs had at most one suspect session per run, and the handoff rerun replaces the handoff table.

**v2 session 23**
- **Next step chosen by me (owner asked):** refused-command memory. It's the only waste Phase 3 showed, it needs no spend to build, and it fits principle 1 (the environment's quirks, which the agent can't know in advance).
- **Read refusals from the transcript, not a new hook.** `PermissionDenied` fires only for auto-mode classifier denials, and `PostToolUseFailure` only after a tool ran. `PreToolUse` sees every attempt but not its outcome. The transcript has both.
- **Warn, don't deny.** The warning is `additionalContext` with no permission decision, so an interactive user can still approve.
- **Listed after two refusals, in one session or several.** A permission doesn't change between tries, unlike a flaky test; a single refusal may be a one-off.
- **Stored as a `step` with a new action name**, not a new event kind: no schema change for stored data.

**v2 Phase 3 (session 22)**
- **The stall run was seeded from the question 67 lean marathon run** (7 session episodes): normal work on the same codebase, and already on disk, so no extra spend.
- **Marathon arms ran in parallel** to halve the wall time. They share the usage limit, so both stopped early. Tickets 1–6 are compared, the stretch both finished.
- **`BLOCKED.md` is judged on the lines the session added** (from its commit's diff), not on a prefix of the whole file.
- **Numbered items under a rule lead-in inherit its classification**, and "1." is no longer a sentence end. A plain numbered list of steps is still not a rule.
- **Nothing is switched on.** None of the three gates showed a gain over lean.

**v2 Phase 2 (session 21)**
- **Order: re-anchoring, stall detection, surprise.** Re-anchoring extends the composer. Stall detection is the owner's "stop endless trial and error". Surprise is plumbing whose effect shows only over many sessions.
- **Re-anchor only on `compact`.** A resumed session still has its conversation. The compact start's context stays in the compacted conversation, so a resume after it needs nothing more.
- **Stall means "the same check failing with no more tests passing", not "many failing runs".** Hard but solvable work fails too, but makes progress; the pass criterion is zero false alarms. Runs without failing ids never count.
- **The floor is 4 stuck runs, raised to the forecast's 90th percentile plus 2.** Seeded normal history has a low 90th percentile, so the floor protects the hard control ticket. A project whose normal work needs long streaks raises the bar by itself.
- **Quiet when similar sessions that got as far mostly ended green** (80 %, at least 2 sessions). This uses track C's `so_far` question. It covers a project where most work is easy but hard tickets still finish.
- **One warning per session, suggesting a stop only for causes the agent may not change.** A second warning would add pressure without new information. Stopping is right only for real blockers; otherwise the advice is to change approach.
- **Surprise defaults to `auto`, as the decision layer's world model does (question 53).** It switches only after the same gate (30 pairs, one-sided 95 %). It changes only what memory learns from, never what the agent sees.
- **A zero-confidence forecast is not a prediction.** Recording the bare prior as `predicted` would skip memory's halved learning rate for unpredicted outcomes.
- **The new prediction source is `world_model`,** so memory tracks the forecast's calibration apart from the similar-episodes fallback (`memory`).

**v2 Phase 1 track D (session 20)**
- **Pick by value, not size.** Horizon's injection is about 300 tokens against 0.5–1.9M a session spends, so saving characters barely moves cost. What moves it is what the context makes the agent do (Q67). The composer therefore fixes what is missing (older constraints) and what misleads (preamble-only labels, the note on clean histories), and it also comes out shorter.
- **Behind `HORIZON_CONTEXT`, classic by default.** Principle 5 applies even though the constraint window is arguably a bug. The candidate arm runs it, and `--set HORIZON_CONTEXT=classic` separates it from session memory.
- **Keep order: active task, team rules, constraints, pitfalls, recent tasks.** Rules and constraints are information the agent can't get any other way (principle 1). Pitfalls are backed by test history. Recent tasks are visible in the code.
- **Strict priority when over budget.** Once a line is dropped for space, less essential sections are skipped, so a short recent-task label can't take the place of a dropped pitfall.
- **The preamble is the prefix shared by the project's recent goals (the last 10), cut at its last sentence.** A pairwise shortest cut stripped "Ticket #" as well.
- **Recent-task labels are at most 120 characters.** That still names the feature, and on the real data it makes the block shorter than classic.
- **The forecast gate uses the base-rate prior, not Jev,** because a Jev call would add about 0.5 s to the first prompt. The threshold is 0.5 expected failing runs: at least every other similar session hit trouble.
- **Every constraint of the project, up to 20, newest first,** with the budget as the ceiling. A later task that changes a rule shows above the old one, and the heading says constraints hold "unless the user changes them".
- **Hosted lean gets the composer too** (server setting), so both installs behave the same once it passes.

**v2 Phase 1 track C (session 19)**
- **C before D.** Stall detection and the composer's "when to show" need a forecast, and C can be evaluated offline for free. D is also easier to judge once "how risky is this task" exists.
- **Forecast sessions, not options.** v2's ground truth is the session (tests at its end, failing runs on the way). `kernel-v1` stays for the decision layer's per-option forecasts.
- **Forecast failing runs and their p90, not just success.** Stall detection needs "is this more failing runs than sessions like it usually take?", and the owner's goal is less trial and error. A p90 or median is given only once there is at least 1 effective session behind it.
- **Reuse `kernel-v1`'s kernel and settings** (width 0.1, `HORIZON_WORLD_MODEL_MIN_SIMILARITY`, `HORIZON_OTHER_PROJECT_FACTOR`). This keeps one notion of "similar" across both models, with no new knobs.
- **The Jev prior is blended with the project's base rate by the project's session count, not used alone.** On real logs, Jev alone was pessimistic about a capable agent (Brier 0.28), since it reads only the task. Jev leads in a new project and fades as the project's own history grows.
- **Jev answers failing runs on 5 ordered levels** (none, 1, 2–3, 4–6, 7+), mapped to 0, 1, 2.5, 5 and 8 and interpolated by its score. Ordered levels are Jev's calibrated shape, and a raw count isn't.
- **A running session (`so_far`) gets no Jev prior.** Jev reads the task, not how the session is going. The prior is the base rate of sessions that got as far.
- **Nothing reads the forecast yet.** Wiring it into surprise and stall detection is Phase 2, and only if `eval-forecast` shows it is clearly better (principle 5).

**v2 Phase 1: Q65, Q66 and track B (session 18)**
- **Q66: close idle sessions after 2 hours (configurable).** A crash, a killed terminal or a closed laptop leaves no `SessionEnd`. Of the options, a timeout is the only one that catches all of these without the host's help. 2 hours is long enough that a lunch break doesn't split a session. If a split session resumes, its rest becomes its own segment, like a resumed invocation.
- **Catch-up also runs at lean's session start (this reverses track A's "never at session start").** It costs nothing when nothing is idle, and 0.7 s only on the first start after a crash. It runs before the start is logged, so a session's own resume can close its idle part.
- **Q65: surface past sessions, but behind a setting, and measure it.** The owner wants good context to cut trial and error. Principle 5 (nothing on by default until it beats the gate) is in `PROJECT.md`, so turning it on by default would contradict the docs. Instead it's built fully, and the evals' `candidate` arm runs it against `lean`.
- **The note shows facts from the log, not advice.** It gives how a similar session ended, the files it changed (where to look), the tests that tripped it (what to check first) and what was still failing (what not to repeat). These are what save the "try this, then that" loop. Wording is "what happened then; check it still holds", so stale history doesn't read as an instruction.
- **Shown at a task's first message, not at session start.** The goal is only known then; session start has nothing to match on.
- **Only past sessions that hit trouble are shown (after the first real run).** A clean success holds nothing to avoid, and its file list cost reading (+29 % turns in marathon tickets 1–6).
- **Relevance floor 0.8 (fastembed), well above recall's 0.2.** Unasked-for context has to be clearly relevant: related tasks score 0.87 or more, unrelated ones about 0.72. Wrong context costs tokens and misleads.
- **1,200-character cap, at most 3 sessions, identical outcomes merged.** It's about 300 tokens, once per task.
- **Local only for now.** Hosted memory lives on the server, and hosted doesn't forward events yet (question 60).
- **Track B: the judge uses the owner's configured scorer (`HORIZON_SCORER`), not a new setting.** It's the same opt-in as the rule classifier, and nothing leaves the machine unless the owner chose a scorer. So no principle 5 change is needed: without a scorer, behaviour is unchanged.
- **Judged outcomes are `implicit`.** Memory already weighs these below test runs (0.4 against 0.7), and a judgement is weaker evidence than a test. No schema change.
- **The judge reads prompts and counts only.** It gets the task, follow-up messages (redacted) and the numbers of edits, files, commands and turns, never paths. Follow-up messages are the real evidence: "still broken" versus "thanks, works".
- **Judge only with evidence; drop unsure or contradicted verdicts.** A lone question with no edit isn't judged (no paying Jev to guess). A verdict with confidence below 0.5 is dropped, and so is "done" when the last message is a correction.
- **A failing judge marks the segment skipped rather than pending,** so a bad key can't cause a retry storm at every session end. `--rejudge` covers recovery.
- **The configured scorer is also the attention filter on lean's memory,** matching the MCP server. It's the "relevant?" classifier question.

**v2 Phase 1 track A (session 17)**
- **The unit is a segment ending at `session_end`, not a whole session.** Headless resumed sessions (`--resume`) end once per invocation, and each invocation is a separate piece of work with its own prompt and result.
- **The option is `session`,** with task type `session` as a condition. A session isn't one decision, and a fixed label keeps consolidation and predictor stats for sessions apart from decisions'.
- **No test run means no episode.** Without tests there's no ground truth, and track B's outcome judge fills that gap. The skip is recorded so it isn't retried; track B can re-derive from the log.
- **Written only at lean's `SessionEnd`, never at session start.** Loading the embedder takes about 0.7 s, which is fine at exit but not before the agent's first turn. Missed segments are caught up at the next end. (Superseded in session 18: session start also catches up idle sessions.)
- **Not under observe, full or hosted.** Observe stays a pure recorder, and its episodes can be derived later with `episodes-from-log`. Full already writes `record_outcome` episodes. Hosted doesn't forward events.
- **A new additive table, `session_episodes`, records whether each segment was written or skipped.** The episode schema is unchanged, so no stored data changes.

**v2 Phase 1 track E (session 16)**
- **observe:** it reuses lean's hook set and matchers, with a handler that returns nothing, so its event log is the same as lean's by construction. It's local only, because hosted installs don't send events yet.
- **`injected_chars` goes on each hook call's first event.** Summing it per session gives the context cost without a new event kind.
- **Repeat failures are counted per check id** (pytest `FAILED`/`ERROR` lines in the agent's own runs). The policies and quirks show up there first.
- **A regression is a hidden test that passed after the previous session and doesn't now.** Separate-repo scenarios (`team`) can't regress.
- **The marathon codebase is about 400 lines, not about 800.** That's enough for 5 policies and 24 tickets that roughly triple it; more lines would only add reading cost.
- **Marathon backups use format v2** (`{"version": 2, "tables": …}`), with v1 still accepted on import, so ticket 12 is a real migration.
- **Decimal is allowed.** Only float literals and `float()` calls are drift, since the rule is about floats.
- **The marathon reference is one final solution checked against all hidden tests,** not one solution per ticket. Every ticket's tests stay in the cumulative set, so it's equivalent.
- **A compaction session is a list of prompts, each resuming the previous session id.** A usage limit stops the remaining invocations.
- **Compactions are counted from stream events whose `system` subtype contains "compact".** The exact marker is confirmed by the first real run.
- **`utc_only` flags local or naive clocks,** `astimezone()` with no zone and `ZoneInfo`. Offsets built by hand are left to the hidden tests.
- **Stall reuses the marathon codebase through `start_from` plus an overlay,** rather than a third codebase. Its controls reuse marathon tickets and hidden tests, and a test keeps the copies identical.
- **Stall is scored with `BLOCKED.md`, not hidden tests.** The harness diffs the session's commit and matches a per-ticket reason pattern; hidden tests only catch faking. Every ticket's prompt carries the same `BLOCKED.md` line, so nothing marks the stall tickets.
- **Ticket #3's silence policy is behavioural** (it captures stdout and stderr), so `sys.stdout.write` can't get around it. The hidden check also runs the library outside pytest, to catch printing only when tests aren't running.
- **Seeding replays full sessions through the arm's own hooks,** as subprocesses and in order. Before, only test runs were replayed; the forecast needs whole sessions.

**Long-horizon eval (session 13)**
- The hidden tests and checks run outside the repo, so neither arm can see or overfit them.
- One run per arm to start; repeat before believing a difference.
- Lean is the default for Claude Code (owner go-ahead). The reliability demo pins `--profile full`, since it measures the full workflow's tool calls. The benchmark's `--with-horizon` uses the default, the product as shipped.
- Team rules: the latest task that sets `team_rules` holds the current set, so the user can drop a rule. It's read from the last 50 tasks' data, with no new table and no schema change.
- SessionStart shows the last 3 completed tasks in the project (goal plus up to 10 constraints). Cheaper than resuming tasks automatically, and it keeps "one task per request" intact.

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

**Session 12 decisions (the review pass)**
- **A severe outcome escalates at once**, with no retries: §9 asks for a human when stakes are high, and a severe outcome is exactly that.
- **Scope:** the same task 1.0, the same project 0.95, project-less memories 1.0, other projects `other_project_factor` 0.85. The owner's research favours walls; the default keeps §10's "prefer", and 0 gives walls.
- **Scorer bias correction** starts after 20 outcomes and is clamped to ±0.5. The raw prediction is recorded, so trust keeps measuring the scorer and not its corrected output.
- **The baseline-echo guard** is limited to the first `record_outcome` of a task, with host test counts, when hooks saw no new run. That's narrow enough never to eat a real outcome.
- **Hosted hook scope** is `<team>/<project key>` on the server. Clients never choose the team part.
- **`purge-memory` is operator-only CLI, never an MCP tool:** an agent must not be able to erase evidence. It's the single exception to "episodes are never deleted", for legal requests.
- **The Claude Code benchmark** shares one Horizon memory across the tasks and repeats of an invocation (§14's repeated-run curve). Setup files are committed before the agent runs, so patches contain only the agent's change.
- **The hook timeout is 30 s** (was 10). One hook was cancelled on a loaded machine.
- **Codex MCP env passthrough and pre-approval** stay as set in session 11.

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

**v2 Phase 0 decisions (session 15, defaults under the decision rule)**
- **Events are observations, and state is derived.** How to represent state is the open research question, so the log keeps raw, redacted observations. Adapters derive state, and a better representation can be re-derived later from the same history.
- **The schema has no domain fields.** Coding details live in `action.name`, `result.metrics`, `result.facts` and `data`, so a browser or SDK adapter needs no schema change.
- **Reads aren't logged.** They change nothing and would multiply the log; they can be added later with a matcher change.
- **Edits are captured in lean only.** Full keeps its Bash-only PostToolUse matcher because the reliability demo pins full and measures its hook traffic.
- **Hosted mode doesn't forward events yet**, and `PreCompact`/`SessionEnd` are installed only for local lean. Hosted forwarding needs a server endpoint and a privacy review; `sanitise` is already reusable for it.
- **Risk is judged on the raw command, before redaction**, and only the flag and category are stored. The stored target is redacted, so a secret in a command never lands in the log.
- **`git push` counts as risky (publish); `pip install -r`, `-e`, `.` and a bare `npm install` don't** (they install what's already declared). Changing a manifest or lockfile is risky.
- **A non-test bash command's `ok` is unknown (None)** unless it was interrupted: hooks don't get the exit code, and guessing from output would be a self-report.
- **`turn_end` is skipped when `stop_hook_active`**, so a Stop hook that continues the turn doesn't count it twice.
- **The host name comes from `HORIZON_HOST`** (default `claude-code`). Codex's `apply_patch` is recorded as a generic `tool` for now.
- **Test data is duplicated for now:** `TestCapture` still drives pitfalls, and the event log records the same runs. Pitfalls can later be derived from events, which removes the duplicate.
- **An event-log failure is swallowed separately** from the hook handler, so the hook's output is identical whether logging works or not.

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

| 56 | Make `--profile lean` the default install? | **Done, owner-approved (2026-09-30):** lean is the default for Claude Code; `--profile full` keeps the whole workflow. Codex stays full until its `UserPromptSubmit` support is checked. See PROJECT.md §0. |
| 57 | How to tell Codex sessions from Claude Code in the event log | Default: the `HORIZON_HOST` environment variable (default `claude-code`); Codex's installer can set it once Codex gets the lean profile. |
| 58 | Should Horizon ship an `observe` profile (log events, inject nothing) to collect baseline data from plain sessions? | **Built** (session 16) and documented in `CLAUDE_CODE.md`, since it's harmless and local. Whether to advertise it to users is still the owner's call. |
| 59 | How the `compaction` eval forces a compaction | **Built** as F1: `/compact` on a resumed headless session. The long-transcript fallback (F2) is built only if F1 fails (question 62). |
| 60 | Forward events in hosted mode? | Default: not yet. It needs a server endpoint and a privacy review; Phase 4's team sharing is the first place it's needed. |
| 61 | Should Phase 1 start, and with which tracks? | **Owner chose track E first** (session 16); it's built. Tracks A–D next. |
| 62 | Does F1 really compact a headless session, and what does the stream show? | **Verified (session 22):** one `compact_boundary` per run and a `compact` session start in the event log. Gate run done: re-anchoring had zero violations after compaction but no gain over lean, so it stays off. |
| 63 | Which run seeds `stall`? | Default: a baseline run of `marathon`'s first tickets (normal work on the same codebase), passed as `--seed`. Stall detection itself is Phase 2, so nothing reads the seed yet. |
| 65 | Should session episodes surface in the next session, or only feed learning until the context composer (track D) decides what to show? | **Owner decided to surface them (session 18).** A new task's first message gets what similar past sessions taught, to cut trial and error. It's built behind `HORIZON_SESSION_MEMORY` and turned on in the evals' `candidate` arm. It becomes the default once `candidate` beats `lean` on `marathon` (principle 5), or earlier if the owner overrides principle 5 for it (question 67). |
| 66 | Sessions that end without `SessionEnd` (a crash, a killed terminal) never become episodes | **Resolved (session 18):** closed after 2 hours idle (`HORIZON_SESSION_IDLE_MINUTES`), at the next session start or end. |
| 67 | Turn `HORIZON_SESSION_MEMORY` on by default? | **Owner approved a short real run (session 18).** Marathon tickets 1–6, one run each: same quality (17/17, 0 violations), but `candidate` cost more (+29 % turns, +44 % tokens, +33 % cost; `evals/longhorizon/RESULTS.md`). Earlier tickets had all passed cleanly, so the note held only file lists. **It stays off.** The note now shows only sessions that hit trouble. Next check: a later stretch of marathon, where the CI policies trip tickets (e.g. `--resume` from a lean run at ticket 9, tickets 9–16, 2 repeats), once the usage limit allows. |
| 68 | The TypeSafe key was pasted in chat | It's used only as an environment variable and not stored anywhere in the repo. The owner should **rotate it** when convenient. |
| 69 | Jev judged "No, refunds over the order total must be rejected." as a correction at only 0.26 | Default: that's fine. A new requirement isn't quite a correction, and the correction answers only block contradicted verdicts; they don't set the score. Revisit with real judged sessions. |
| 70 | When the session forecast beats the fallback on replay, should Phase 2 switch surprise to it by itself (like `HORIZON_WORLD_MODEL=auto`)? | Default: yes, behind the same gate as `kernel-v1`: at least 30 paired sessions and a one-sided paired test at 95 %, re-checked periodically. It can be decided when Phase 2 wires it. |
| 71 | One latency script hung before its first forecast and couldn't be reproduced (the same script then ran at 12–20 ms per forecast) | Default: most likely the first fastembed model load waiting on its cache lock, not the forecast code. Watch hook latency in the next real run. |
| 72 | The gate run for Phase 1's context: `candidate` (session memory plus the composer) against `lean` on `marathon` | **Run (session 22):** tickets 1–6 compared (both arms hit the usage limit). Both clean; candidate −10 % tokens, within noise. Stays off; next is question 78. |
| 73 | Should classic lean's constraint window (the last three tasks) be fixed even if the composer as a whole doesn't pass? | Default: no, not separately. The composer carries the fix; if the gate fails on cost only, the constraint part can be split out and tested alone. |
| 74 | The `stall` gate run: `candidate` against `baseline`, seeded from a marathon baseline run (`--seed`) | **Run (session 22):** zero false alarms, but also zero failing-run streaks, so the detector never fired. Candidate wasn't faster to stop. Stays off; see question 76. |
| 75 | Should hosted installs get stall detection? | Default: later, with event forwarding (question 60). It needs the event log and the forecast on the server. |
| 76 | Stall detection watches only failing test runs, but the real stall (candidate's ticket 5) was repeated probing with ordinary commands | **Partly answered (session 23):** the probing was refused calls, now caught by refused-command memory (`HORIZON_DEAD_ENDS`). Commands that run but keep failing the same way (e.g. `command not found`) are still not watched; default: wait for the real check. |
| 77 | Part 6 of `compaction` fails in every arm, even with the UTC rule restated | Default: keep it as a judgement trap and report it separately. It measures interpreting a rule, not remembering it, so re-anchoring can't fix it. |
| 81 | Cursor's backend dropped two of three long runs (tickets 16 and 22) | Default: compare on the tickets every arm finished. If it keeps happening, add a `--from-ticket` restart that reuses the repo state. |
| 82 | On Cursor, both Horizon arms missed a ticket 4 hidden test that baseline passed (1 run each) | **Answered (session 24):** in 3 repeats per arm, baseline misses the same `open_store(None)` test too. Including the marathon run, ticket 4 passed 3 of 4 times for baseline, 2 of 4 for lean and 1 of 4 for candidate, which is noise (p ≈ 0.5). |
| 83 | Lean's hooks add about 0.6 s per tool call on Cursor (+21 % wall time over 12 tickets) | Default: next engineering task. Measured: one hook takes 0.21 s, nearly all of it imports (event log pydantic models 90 ms, `horizon.models` 35 ms, numpy through `horizon.db` 40 ms; Python start-up 0.02 s). Make imports lazy first; one long-lived process the hooks talk to only if that isn't enough. |
| 84 | Over 3 repeats, lean matched baseline on hidden tests and tokens on marathon tickets 1–12 | Default: the marathon's remaining misses are hidden behaviours that visible tests never show, which no history can carry. Lean's gain, if any, needs a scenario where earlier sessions' evidence decides a later ticket. |
| 85 | Cursor replaces secret-looking environment variables with `...` in hooks, so Jev can't run in Cursor hooks | **Answered (owner): the macOS Keychain.** The key is stored as the `horizon-typesafe` item, and `typesafe_key()` reads it when `TYPESAFE_API_KEY` is missing or hidden. Verified: under Cursor with no key in the environment, Jev captured ticket 1's rules. Remove it with `security delete-generic-password -s horizon-typesafe`. |
| 86 | Handoff: rules survive exactly when the agent writes them into the README in ticket 1 | **Answered (isolated rerun):** with Jev, every rule survived in all 6 runs, 5 of which wrote nothing down; baseline 2 of 12 rule checks. The earlier runs could read the hidden tests and are superseded. |
| 87 | Lean + Jev beat baseline on handoff (24/24 rule checks against 2/12, 3 runs each, equal tokens). Make Jev lean's default rule splitter? | Default: not yet. It needs a TypeSafe key on every install, which is the owner's (account and pricing, question 37). Keywords stay the default. Next evidence: the same arms on a second scenario. |
| 88 | Jev also stores ticket specs, preambles and "Run ./ci.sh" as constraints | **Now costs results (rental):** 43–46 stored constraints, and the newest-20 cap dropped the ticket 2 phone rule by ticket 11, so 1 of 3 runs passed it. Default fix, next: ask Jev to keep only rules that apply beyond the current ticket, and drop sentences repeated across tickets. Then rerun rental. |
| 91 | The real RentEase product (owner chose "both": eval first, then a real product in a separate repo) | **Owner.** It needs: payment, KYC and e-sign/e-stamp accounts; a licensed NBFC-P2P or lending partner (RBI rules forbid pooling and guaranteed returns); a legal opinion on deposit placement and on marketing it as an investment (SEBI); and no scraping of owners' personal data (DPDP Act, portal terms), so opt-in listings instead. Default: build it on providers' sandbox APIs and switch to live once accounts and licences exist. |
| 89 | Keyword lean wrote the dates rule into the repo in 3 of 3 runs, baseline in 1 of 3 | Default: treat it as noise until repeated. Lean's session-start text doesn't ask for notes, but it shows the fines rule, which could prompt writing the others down. |
| 90 | Agents read Cursor's own transcripts of earlier sessions (`~/.cursor/projects/…/agent-transcripts`) in every arm | Default: allowed. It's Cursor's built-in memory and part of the baseline. Counted as `agent_memory`; it didn't carry the rules here. |
| 79 | Should refused-command memory try to name the way that worked (e.g. "write the script in the repo, run plain `python3`")? | Default: no. Inferring the workaround from the next successful call is guesswork; naming what's refused already rules out the dead end. Revisit if the real check shows the agent still loops. |
| 80 | Lean now runs a hook before every shell command (about 0.2 s), even with `HORIZON_DEAD_ENDS` off | Default: accept it; lean already runs one after every command. If it matters, the installer could add the `Bash` matcher only when the setting is on. |
| 78 | Marathon showed no difference on tickets 1–6, where little goes wrong | Default: the informative stretch is later tickets, where CI policies trip (question 67's plan): `--resume` from a lean run at ticket 7, tickets 7–14, one arm at a time so the usage limit doesn't cut both. **Owner** (subscription). |
| 64 | `pip install` is reachable through `Bash(python:*)` in every scenario | Unchanged from v1 (both arms get the same tools). The stall environment trap uses a system binary, so no package install can "fix" it, and no scenario tempts a package name that could be squatted. |

**Needs the owner** (credentials, money or an irreversible decision): 91 (the real RentEase product's accounts, licensed partner and legal opinion), 87 (Jev as lean's default splitter needs a TypeSafe key per install), 78 (marathon's later tickets) or 76 (extend stall detection), 68 (rotate the TypeSafe key), 27–28 (real benchmark runs), 37 (Jev pricing and terms), 38 (running the small-LLM scorer), 47 (the erasure policy), 48 (pricing and payments), 50 (tracing), making the repo public, and the hosting provider.

## Next step

**v2 (this copy):** Phase 1 of `PROJECT.md` §0 is built: tracks E (session 16), A (17), B (18), C (19) and D (20). Next:
- **Phase 3 gate runs are done (session 22):** no gated component beat lean, so Phase 4 doesn't start (principle 5).
- **Refused-command memory is built (session 23).** Its real check is queued (`/tmp/lh-de`). Next: read it against the Phase 3 runs (`refused_calls`, tokens on stall ticket 5, marathon tickets 1–8), and record it in `RESULTS.md`. If it cuts refused calls without costing tokens, run `lean` on the same tickets for a paired comparison. Then marathon's later tickets (question 78).

- **Cursor works as a second agent (session 24)** at no subscription cost. Ticket 4 repeats showed the gap was noise (question 82). Three repeats of baseline against lean then showed no quality or token difference, and lean 21 % slower (questions 83 and 84). Hook latency is fixed (question 83), and the handoff scenario exists (question 84). Jev's key reaches Cursor hooks (question 85). The eval harness now isolates agents and measures contamination. In the isolated handoff rerun, lean + Jev carried every early rule to the late tickets (24 of 24 checks against baseline's 2 of 12, question 86). The second scenario (`rental`, guarded) confirmed it: 22 of 27 rule checks against 8, at 56 % of baseline's tokens. **Next:** fix Jev's over-capture (question 88) and rerun rental. Then the owner decides on the real RentEase product (question 91) and on Jev as the default splitter (question 87). After that, use Cursor for the repeated runs the Claude usage limit keeps cutting short. Whether any component beats lean needs a scenario where visible-test history predicts the hidden failure, and marathon ticket 4 isn't one.

**Owner, when convenient:** rotate the TypeSafe key (question 68). Once the usage limit resets, the `candidate` against `lean` gate run on `marathon` (question 72).

**Owner, when convenient:** one real `compaction` run to verify F1 (question 62). Real marathon runs wait until the Phase 1–2 components exist (`docs/EVALS.md` budget).

**Carried over from v1:**

1. **Owner:** the "Needs the owner" line above. The launch waits on making the repo public, benchmark credits, a hosting provider and pricing (`docs/LAUNCH.md`).
2. Merge the review-pass PR (it includes Phase 8; PR #9 is superseded).
3. Run the platform benchmark: `horizon-bench run --stage full --agent claude-code` then `--with-horizon`, 3 repeats each, after `real_runs: true`.
