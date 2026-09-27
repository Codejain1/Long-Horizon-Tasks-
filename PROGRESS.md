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

## In progress

- Nothing. Phase 1 still needs its committed task list (see below). The only remaining Phase 1 step is generating and committing the task list once `huggingface.co` is reachable.

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

11. **Jev access is unverified** (§15). Should the small-LLM comparison scorer (§5) be built first as a fallback behind the same interface?
12. **Thresholds undefined.** There are no values yet for the "clear winner" and "close call" thresholds, the score weights, or crucial-decision detection.
13. **"Ask human if high stakes."** A concrete rule is needed beyond the examples given.

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

26. **(Blocks the committed task list.) `huggingface.co` is blocked in the cloud session**, so `horizon-bench select` can't read SWE-bench Verified. [Everything else is built and tested on a synthetic pool. Allowing `huggingface.co` and `*.hf.co` (the same domains as the fastembed download) unblocks it: then run `horizon-bench select` and commit `bench/tasks/swebench_verified_50.json`.]
27. **Verify on the first real smoke run:**
    - litellm's Anthropic usage fields: the harness assumes `prompt_tokens` includes cache reads and writes;
    - the key sb-cli uses for resolved ids in its report: the harness reads `resolved_ids`, falling back to `resolved`.
    [Both are handled defensively, and the smoke run exists to catch exactly this.]
28. **Real-run spend.** The caps bound the worst case at $10 for the smoke run and $150 for the full 50 × 3. [No real runs until the owner sets `real_runs: true` and provides the keys.]

### Raised in Phase 3 (the default used is in brackets)

29. **Repos with failing tests before the task starts** would trigger a rollback on every attempt at the 1.0 threshold. [Threshold 1.0, configurable. The better rule is "worse than the pass rate at the checkpoint", but that needs a test run at checkpoint time.]
30. **Hosts without hooks get no checkpoints.** [Claude Code only, per decision 19. A `checkpoint` parameter on `recall_context` would cover Codex and Cursor later.]
31. **Should a `severe` outcome escalate immediately** instead of after N attempts? [No. It follows the normal limit.]
32. **We don't verify that the host actually restored.** [Streaks always target the first good checkpoint, which limits the damage. A hook could compare the tree with the snapshot on the next recall.]
33. **Phase 3 has no benchmark run** (§13). [Same as open question 10: it doesn't change the baseline agent, which is mini-SWE-agent without MCP.]

## Next step

1. **Owner:**
   - allow `huggingface.co` and `*.hf.co` in the environment's network settings;
   - optionally run `demo/reliability/run.sh` to measure how reliably Claude Code calls the tools.
2. Run `horizon-bench select` and commit the 50-task list. Review and merge PR #3.
3. When credits and keys exist, set `real_runs: true` in `bench/config.yaml`:
   - smoke run first (`horizon-bench run --stage smoke`, at most $10);
   - then the full baseline (`--stage full`, 3 repeats, at most $150).
4. Review and merge PR #3, then the Phase 3 PR stacked on it.
5. Optionally, run a real Claude Code session on `demo/reliability/` and check that `PreToolUse` fires on `recall_context` and that a failing task gets a rollback.
6. Then **Phase 4: decision layer** (crucial-decision detection, Jev scoring, weights and thresholds). Open questions 11–13 need answers or defaults first.
