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

## In progress

- Nothing. Phases 1–3 are complete in dry-run form. What remains needs owner action: real benchmark runs (API credits and keys).

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
4. **(Resolved: owner approved the default — same Postgres, separate tables, failure-isolated code path.) Task state and memory storage.** Task state must keep working if memrouter is down (MEMROUTER §11). Should it be a separate service or database, or the same Postgres with separate tables and a failure-isolated code path?
5. **(Resolved: owner approved the default — stdio without auth, static dev key for HTTP.) When does API key auth start?** Key, credits and usage endpoints are Phase 7. Should Phases 2–6 use stdio locally with no auth, plus a single static dev key for HTTP?

### Phase 1: benchmark harness

6. **(Resolved: owner approved (a) with (b) as fallback.) Where does the agent execute task code?** mini-SWE-agent's SWE-bench mode normally runs each task inside that task's SWE-bench Docker image. The cloud session may not have Docker. The options are:
   - (a) Docker in the session, if available (pulls images from Docker Hub, so that domain would be needed);
   - (b) a local environment per task (clone the repo and install its dependencies; slower and can differ from the evaluation environment);
   - (c) a remote sandbox provider (a new service and credential).

   sb-cli only covers evaluation, not the agent's own runs. Proposal: support (a) with (b) as a fallback, and check which one works when real runs are enabled. This doesn't block the dry-run build.
7. **(Resolved: weights approved.) Difficulty weighting.** SWE-bench Verified has four difficulty buckets (<15 min, 15 min–1 h, 1–4 h, >4 h), and the longest has very few tasks. Proposal:
   - take every >4 h task;
   - then roughly 40% from 1–4 h, 35% from 15 min–1 h and 25% from <15 min;
   - stratify by repo within each bucket.

   Are those weights OK?
8. **(Resolved: first 10 of the 50.) Smoke run selection.** Should the 10 smoke tasks be the first 10 of the committed 50 (keeping the same stratification), or a separate set?
9. **(Resolved: per-token prices in config.) Model pricing.** mini-SWE-agent tracks cost through litellm. If litellm doesn't yet list `claude-sonnet-5` prices, the $1 cap can't be enforced. Proposal: set per-token prices in harness config and use them for both the cap and the reports.
10. **"Benchmark after every phase".** Phases 2–3 don't change agent behaviour much. Is a no-regression check enough for those phases?

### Decision layer and Jev

11. **(Default approved in round 5.)** **Jev access is unverified** (§15). Should the small-LLM comparison scorer (§5) be built first as a fallback behind the same interface?
12. **(Default approved in round 5; values are set in the Phase 4 plan.)** **Thresholds undefined.** There are no values yet for the "clear winner" and "close call" thresholds, the score weights, or crucial-decision detection.
13. **(Default approved in round 5; the rule is set in the Phase 4 plan.)** **"Ask human if high stakes."** A concrete rule is needed beyond the examples given.

### Memrouter spec gaps

14. **(Resolved: owner approved ratio 1.) Surprise when predicted and actual are both 0.** With `max(actual, ε)`, the ratio comes out as 0 instead of 1 (for example, a predicted cost of $0 when the actual cost is $0). Should this case count as a ratio of 1?
15. **Decay maths is undefined.** No formulas are given for how `strength` and `stability` change per recall or per "usage opportunity", and "helpful" isn't defined.
16. **Undefined terms.** "Condition match" scoring for mixed operators is not defined. Neither is who classifies a failure as "severe".

### Outcomes, privacy and data

17. **Undefined specs.** "Heavy testing" and the world-model logging format are both undefined. The logging format is needed from Phase 1/2 ("from day one"). Proposal: Phase 1 writes one JSON Lines record per task run with a versioned schema, and the Phase 2 episode format builds on it.
18. **(Resolved: counts only for test results, plus redaction of decision text; see round 4.) Raw code could be stored.** Test output and `testResults` can contain code, stack traces and file paths. What sanitisation or redaction is required?

### Host integration

19. **(Resolved: Claude Code only.) Which hosts first?** Should Phase 2 target Claude Code only, or also Codex and Cursor? What target invocation rate counts as "reliable"?

### Raised in Phase 2 (non-blocking; the default used is in brackets)

20. **(Resolved: build Phase 1 next, dry run, no paid calls.) Phase 1 was skipped.** The harness isn't in the repo yet, but this session was asked to build Phase 2. §13 says to benchmark after every phase, so Phase 2 has no benchmark run. [Built Phase 2 as asked. Phase 1 is still to do, dry run first, with no paid calls.]
21. **(Resolved: cloud Claude Code sessions run on the owner's subscription; reliability demo built in `demo/reliability/`.) Real host invocation rate is not measured yet.** This needs real Claude Code sessions, which cost API usage. [The machinery is in place: hooks plus `horizon stats` → `outcome_recording_rate`. The owner runs a few real tasks when ready.]
22. **(Resolved: `host` approved as a fourth source; `MEMROUTER.md` updated.) `predicted.source`.** MEMROUTER §4 lists `jev | sim | memory`. Phase 2 adds `host`. Should the fallback prediction be tagged `memory`? [Today a missing prediction keeps `source: "host"`, and the fallback is marked by `lowConfidence`.]
23. **(Resolved: basic redaction built.) Situation text can still contain code.** `situation`, `chosen` and `reason` are free text from the host. [No redaction yet. The tool descriptions ask for short summaries.]
24. **(Resolved: keep the fallback; domains listed in round 4.) Embedding model download.** `huggingface.co` must be reachable wherever the server runs, or it falls back to the weaker hash embedder. [Fallback with a warning. `huggingface.co` is already on the §18 domain list.]
25. **(Resolved: keep it, but quiet.) Stop-hook pushiness.** Blocking a stop once to ask for `record_outcome` could annoy users. [Enabled. Removing the `Stop` hook from `.claude/settings.json` turns it off.]

### Raised in Phase 1 (the default used is in brackets)

26. **(Resolved in session 4: generated and committed from the owner's Mac.) `huggingface.co` is blocked in the cloud session**, so `horizon-bench select` can't read SWE-bench Verified. [Everything else is built and tested on a synthetic pool. Allowing `huggingface.co` and `*.hf.co` (the same domains as the fastembed download) unblocks it: then run `horizon-bench select` and commit `bench/tasks/swebench_verified_50.json`.]
27. **Verify on the first real smoke run:**
    - litellm's Anthropic usage fields: the harness assumes `prompt_tokens` includes cache reads and writes;
    - the key sb-cli uses for resolved ids in its report: the harness reads `resolved_ids`, falling back to `resolved`.
    [Both are handled defensively, and the smoke run exists to catch exactly this.]
28. **Real-run spend.** The caps bound the worst case at $10 for the smoke run and $150 for the full 50 × 3. [No real runs until the owner sets `real_runs: true` and provides the keys.]

### Raised in Phase 3 (the default used is in brackets)

29. **(Resolved in round 5; see decisions 23–24.)** **Repos with failing tests before the task starts** would trigger a rollback on every attempt at the 1.0 threshold. [Threshold 1.0, configurable. The better rule is "worse than the pass rate at the checkpoint", but that needs a test run at checkpoint time.]
30. **Hosts without hooks get no checkpoints.** [Claude Code only, per decision 19. A `checkpoint` parameter on `recall_context` would cover Codex and Cursor later.]
31. **Should a `severe` outcome escalate immediately** instead of after N attempts? [No. It follows the normal limit.]
32. **We don't verify that the host actually restored.** [Streaks always target the first good checkpoint, which limits the damage. A hook could compare the tree with the snapshot on the next recall.]
33. **Phase 3 has no benchmark run** (§13). [Same as open question 10: it doesn't change the baseline agent, which is mini-SWE-agent without MCP.]

### Raised in session 4 (the default used is in brackets)

34. **(Resolved in round 5; see decisions 23–24.)** **Surprise is biased negative by design.** `efficiency = min(1, predicted/actual)` can't go above 1, but `predicted_score` assumes efficiency = 1. Coming in under budget earns nothing and any overrun is penalised, so even a perfectly calibrated predictor averages below 0, and Phase 6 links would slowly weaken. [Implemented exactly as `MEMROUTER.md` §5 says, because changing it would contradict the doc. Proposal: use the expected efficiency (for example, the running mean) in `predicted_score`, or let the ratio go above 1 with a cap.]
35. **(Resolved in round 5; see decisions 23–24.)** **Hook-captured test counts can include unrelated failures.** In the demo, a host sometimes ran the whole suite, which included other tasks' stubs, before its targeted run. If that were the last run, `record_outcome` would see a pass rate below 1 and trigger a rollback. [This is the same root cause as open question 29. The default keeps the latest capture. The fix is to compare against the pass rate at the checkpoint.]
36. **Provenance can't tell subagents apart** (MEMROUTER §11). [It uses `client_info` only. An optional `agent_id` parameter on `record_outcome` would cover it when subagents arrive.]

### Raised in Phase 4 (the default used is in brackets)

37. **(Partly resolved in session 7: key provided, Jev verified live; pricing and rate limits still unpublished.)** **Jev has never been called for real.** This needs a `TYPESAFE_API_KEY` from console.typesafe.ai. The docs publish no pricing or rate limits (§15 "test Jev access for real" is still open). [Built and tested against the SDK over a mocked transport; `HORIZON_SCORER=none` by default.]
38. **(Needs credentials and spend.) The LLM scorer has never been called for real.** It needs Claude API credentials. [Same as above.]
39. **Weights and thresholds are untuned.** §5's experiment (Jev against the small LLM on real decisions, then against real outcomes) is what should set them. [Defaults above. Every raw dimension is returned, so re-weighting doesn't need re-scoring.]
40. **Phase 4 has no benchmark run.** mini-SWE-agent doesn't speak MCP (same as open question 10). [The demo measures invocation instead.]

### Raised in Phase 5 (the default used is in brackets)

41. **Jev rarely produces close calls.** Three real library choices all came out as clear winners (margins 0.17–0.24), so consequence checking may seldom run at the 0.10 margin. [Kept 0.10. The decision log will show how often close calls happen; the §6 experiment ("on a sample of ties, run all options for real") needs ties, so a larger margin may be worth it for the experiment.]
42. **Possible high-stakes false positive:** "Celery with Redis vs RQ vs APScheduler" scored as high stakes. [Only matters on a second tie (then a human is asked). Watch it in the decision log.]
43. **Spikes cost the host's tokens and time** (about 6 extra tool calls per option in the live runs). [The plan caps each spike at `HORIZON_SPIKE_BUDGET_MINUTES` (10), static checks can skip a spike, and memory reuse avoids repeats.]

## Next step

1. **Owner:** to use Jev in your own projects, set `TYPESAFE_API_KEY` and `HORIZON_SCORER=jev` in the environment Claude Code starts from. Optionally set `HORIZON_SCORER=llm` with Claude API credentials to run the Jev-vs-small-LLM comparison (open question 39).
2. Review the Phase 4 branch and open its PR when ready (the owner asked for PRs in batches).
3. Phase 5 is on `claude/phase-5-consequences`. Next is **Phase 6: memrouter learning** (surprise-based links, spreading activation, the Jev attention filter, decay, consolidation, fear memories, predictor trust). Open questions 15–16 (decay maths, condition matching) are relevant there.
4. When credits and keys exist, run the Phase 1 real benchmark (smoke run first).
