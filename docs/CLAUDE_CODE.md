# Connecting Horizon to Claude Code

> Using Codex? See [CODEX.md](CODEX.md): same hooks and tools, a different installer. Running a shared server? See [HOSTING.md](HOSTING.md) and `horizon install-claude-code --hosted URL`.

Horizon is an MCP server. Claude Code decides when to call it, so reliable calling comes from three layers (`PROJECT.md` §11):

1. **Directive tool descriptions**: each tool says exactly when it must be called.
2. **Instruction snippet** in the project's `CLAUDE.md` (source: `src/horizon/data/agent.snippet.md`, shared with Codex's `AGENTS.md`).
3. **Hooks** that run on their own, without the model deciding to call them:

| Hook | Event | What it does |
|---|---|---|
| `horizon hook session-start` | `SessionStart` | Injects the workflow rules, the team's rules for every project (`start_task(team_rules=…)`), active tasks for this project (so a resumed session continues the same task), and the goals and constraints of its recently finished tasks (so the next session keeps them). |
| `horizon hook pre-tool-use` | `PreToolUse` (matcher `mcp__horizon__recall_context\|mcp__horizon__record_outcome`) | Just before each `recall_context`, records a **checkpoint reference**: a git commit of the working tree (`git stash create`, or `HEAD` when clean; nothing in the repo changes) and the latest user prompt, which is the Claude Code checkpoint to pick in `/rewind`. The recall attaches it to the task, and the next decision records it. Silent, except right after a rollback: if the working tree doesn't match the rollback target, it **denies that one recall** with the restore command. It does this at most once per rollback, so it never loops. Just before each `record_outcome`, it sums the **token usage and elapsed time** of the model turns since the last `recall_context` from the transcript (counts only), and `record_outcome` records them as the attempt's actual tokens and latency unless the host passed its own. |
| `horizon hook post-tool-use` | `PostToolUse` (matcher `Bash`) | When the command is a test run (pytest, unittest, jest, vitest, go, cargo, rspec, …), it parses the pass/fail **counts** and stores them. `record_outcome` then uses these real counts instead of the model's summary (`PROJECT.md` §9). It also nudges the model to call `record_outcome`. Raw output is never stored. |
| `horizon hook stop` | `Stop` | Quiet by default. It speaks only when an active task has an **unrecorded outcome**: a test run from this session that no `record_outcome` has used. Then it blocks the stop once and asks for `record_outcome`. It nudges at most once per test run and never loops. |

Hooks never break the session: any internal error exits 0 with no output. Set `HORIZON_DEBUG=1` to print errors to stderr.

## Setup

```bash
# 1. Install (Python 3.12+). The [embeddings] extra adds fastembed (BAAI/bge-small-en-v1.5).
pip install "long-horizon-platform[embeddings] @ git+https://github.com/Codejain1/Long-Horizon-Tasks-"

# 2. Wire a project: writes .mcp.json, .claude/settings.json (hooks) and the CLAUDE.md snippet.
cd /path/to/your/project
horizon install-claude-code          # --no-claude-md to skip CLAUDE.md
```

The installer is idempotent. It keeps your existing settings, hooks and MCP servers. It uses the absolute path of the current Python, so hooks work even when the virtualenv isn't on `PATH`.

**One-time approval:** Claude Code asks you to approve project `.mcp.json` servers the first time you run `claude` in the project. To skip the prompt, register the server for yourself instead:

```bash
claude mcp add -s local horizon -- "$(which python)" -m horizon serve
```

Check the connection with `claude mcp list`. It should show `horizon: … √ Connected`.

## Lean profile: continuity without tool calls

`horizon install-claude-code --profile lean` installs Horizon for **task continuity** only. That is the part the long-horizon evals show paying off (`evals/longhorizon/RESULTS.md`). The agent makes **no Horizon calls**; the hooks do the work:

| Hook | Lean behaviour |
|---|---|
| `UserPromptSubmit` (lean only) | The session's first message becomes its task: the goal, with code removed. Later messages become progress notes. Sentences that set lasting rules become **team rules** or **constraints** for this project. With a scorer (`HORIZON_SCORER=jev`), Jev answers two yes/no questions per sentence in one request (about 1 s). Without one, or if it fails, keywords are used ("for every project", "from now on", "never"). On `evals/rules/labelled.json`, Jev scored 97 % and keywords 62 %. |
| `SessionStart` | Marks the previous session's task done. A resumed or compacted session keeps its own. Then it shows the team's rules and the recent tasks' goals and constraints. |
| `PostToolUse` | Still captures test counts, but doesn't ask for `record_outcome`. |
| `Stop` | Never blocks. |

In lean, the server leaves out `start_task`, `recall_context` and `record_outcome`: the hooks capture the task, and those tools' "call this every time" descriptions only drew extra calls. It keeps `evaluate_options` (the prompt hook gives the task id), `submit_consequences`, `show_memories`, `explain_decision`, `delete_memory` and `clear_fear`. **Hosted lean** (`install-claude-code --hosted URL --profile lean`) is hooks only:
- The hooks send the prompt to the team's server, with code removed on your machine first.
- **Team rules are shared by everyone using the team's API keys,** across machines and projects.
- It writes no MCP entry, and removes an earlier one. The hosted MCP endpoint serves the full tool list, whose descriptions draw extra calls, so hosted lean has no optional tools yet.

Re-running `install-claude-code` without `--profile` switches back to the full workflow.

## Configuration

Everything is set through environment variables (see `src/horizon/config.py`):

| Variable | Default | Notes |
|---|---|---|
| `HORIZON_DB_URL` | `sqlite:///~/.horizon/horizon.db` | Or `postgresql://…` (needs pgvector). The server and hooks must use the same value. |
| `HORIZON_EMBEDDER` | `fastembed` | Falls back to `hash` (offline, deterministic) if fastembed or its model download is unavailable. |
| `HORIZON_TEAM_ID` | `local` | Memory is isolated per team. |
| `HORIZON_PROJECT_DIR` | server's working directory | Used to match hook-captured test runs to tasks. |
| `HORIZON_DEV_API_KEY` | none | Required for `horizon serve --transport http`. |
| `HORIZON_ROLLBACK_BELOW` | `1.0` | Only for the absolute fallback (see below): an outcome with a lower test pass rate counts as a failed attempt. |
| `HORIZON_MAX_ATTEMPTS` | `3` | Consecutive failed attempts before the task is escalated to the user. |

## Decision layer (`PROJECT.md` §5)

`evaluate_options(task_id, situation, options)` is called before a crucial choice, with 2–4 options and optional `est_cost_usd` / `est_tokens` / `est_latency_ms` on each. A single scorer request answers every question in parallel:
- whether the decision is **crucial** (hard to reverse, shapes many later steps, or has real cost);
- whether it is **high stakes** (money, messages or data);
- for each option: its **chance of success**, **compatibility**, **architecture fit** and **reversibility**.

The scorer sees the goal, the constraints and the recalled past outcomes as evidence. Code combines the dimensions with the weights and applies these rules:

| `decision` | When |
|---|---|
| `routine` | No crucial signal reaches `HORIZON_CRUCIAL_THRESHOLD` (0.5). |
| `clear_winner` | The top composite leads by at least `HORIZON_CLEAR_MARGIN` (0.10), and its scorer confidence is at least `HORIZON_MIN_CONFIDENCE` (0.5). |
| `close_call` | Otherwise. Among the nearly tied options, it takes the cheaper one (by estimate), else the more reversible one. |
| `ask_human` | A close call that is high stakes (`HORIZON_HIGH_STAKES_THRESHOLD` 0.5, or a matching severe past failure). |
| `unscored` | No scorer is configured, or it failed. Nothing is chosen; `cheapest_by_estimates` lists the options by the host's estimates, as information only. |

The chosen option's predicted success is recorded on the episode when `record_outcome` names that option, with source `jev` or `llm` (`PROJECT.md` §9).

### Consequence checking on close calls (`PROJECT.md` §6)

A close call doesn't pick straight away. The chain runs cheapest first:
1. **Memory:** past spike results for the same option in a similar decision are reused (`HORIZON_SPIKE_REUSE_SIMILARITY` 0.85). If every close option was tested before, the decision is settled from memory (`settled_by: "memory"`). If the rest are confidently forecast by an active world model (below), it is settled from those forecasts (`settled_by: "world_model"`).
2. **Try and roll back:** if every close option is cheap to undo (`HORIZON_TRY_REVERSIBLE_THRESHOLD` 0.7), the task has a git checkpoint, and the stakes aren't high, the decision is `try_and_rollback`. The host implements `chosen`, and the Phase 3 rollback rules restore the checkpoint if the tests regress. The next option to try is in `try_order`.
3. **Otherwise `check_consequences`:** `consequence_plan` lists cheap **static checks** (the dependency resolves, licence and platform fit, lint/type config), plus one **spike** per untested option. Each spike says what to build and what to measure, with a time budget. Spikes are built under `.horizon/spikes/` in the project (git-ignored by the installer) and deleted afterwards.
4. **`submit_consequences(task_id, decision_id, results)`:** structured results per option (`static_checks[{name, passed}]`, `spike{ran, passed, tests_passed, tests_failed, metrics{…}, duration_s}`, one short note). The scorer re-scores the close options with the results as evidence and answers the consequence questions: *would it break existing tests?* and *will it cost noticeably more?* Spike metrics named `latency_ms`, `tokens` or `cost_usd` replace the host's estimates. An option that failed a static check or its spike is eliminated. A second tie doesn't loop: it takes the cheaper or more reversible option, or asks a human if the stakes are high. Spike results are stored in memory for the next similar tie.

### World-model decision log

Every decision is logged as one versioned record (`schema: "horizon.decision"`, `version: 1`) containing:
- the state the scorer saw (goal, constraints, situation, options, recalled past outcomes);
- **every raw scorer answer** from both passes;
- the consequence plan and the submitted results;
- the final decision;
- the outcomes the host later recorded for any of the options (success, tests, tokens, cost, the baseline judgement, surprise, and whether the host followed the decision).

`horizon export-decisions --out decisions.jsonl [--with-outcomes-only]` writes them as JSON Lines, one training example per line: state → option → consequences → outcome.

### World model (`PROJECT.md` §6)

Every `evaluate_options` also asks the **world model** for each option's success, tokens, cost and latency. It takes about 7 ms for 5 options over 5,000 episodes. The forecasts are logged in the decision record (`world_model`).
- **The model** (`kernel-v1`): a similarity-weighted estimate over every past episode about the same option, including archived ones, shrunk towards the team's base rate when evidence is thin. It learns with every outcome and needs no training step. `HORIZON_WORLD_MODEL_CLASS=package.module:Class` plugs in another model (for example one trained on the exports), built as `Class(router, settings)`.
- **The gate:** `horizon eval-world-model` predicts each past episode from strictly earlier ones and compares the model's Brier score with what Jev, the small LLM and the host predicted for the same episodes. It passes only when the model is clearly better (a one-sided paired test at 95 %) than every stand-in with `HORIZON_WORLD_MODEL_MIN_PAIRS` (30) paired outcomes.
- **When it's used:** only while it's active. Then its forecasts fill token, cost and latency estimates the host didn't give. On a close call where every untested option is forecast with confidence ≥ `HORIZON_WORLD_MODEL_SETTLE_CONFIDENCE` (0.6), the forecasts replace spikes as the evidence for the second scoring pass (`settled_by: "world_model"`). This step comes after memory and before try-and-rollback. The scorer still decides.
- **Its accuracy** is tracked as predictor source `world_model` in `horizon stats`.

| Variable | Default | Notes |
|---|---|---|
| `HORIZON_WORLD_MODEL` | `auto` | `auto`: logged, and active once the gate passes (re-checked every `HORIZON_WORLD_MODEL_GATE_TTL_S`, 600 s). `shadow`: logged only. `on`: always active. `off`. |
| `HORIZON_WORLD_MODEL_CLASS` | none | Plug in another world model. |

| Variable | Default | Notes |
|---|---|---|
| `HORIZON_SCORER` | `none` | `jev` (needs `TYPESAFE_API_KEY`) or `llm`, the small-LLM comparison scorer (needs Claude API credentials). Install the `[decision]` extra. |
| `HORIZON_JEV_MODEL` / `HORIZON_LLM_SCORER_MODEL` | `jev-latest` / `claude-haiku-4-5` | |
| `HORIZON_DECISION_WEIGHTS` | success 0.30, compatibility 0.20, architecture_fit 0.20, cost_usd 0.15, tokens 0.10, latency_ms 0.05 | JSON. Renormalised over the dimensions available. |

## Inspection and human approvals (Phase 7)

| Tool | What it does |
|---|---|
| `explain_decision(task_id, decision_id?)` | Why a decision went the way it did: past outcomes used, crucial signals, scores per option and pass, consequence checks, eliminated options, and what happened afterwards. |
| `show_memories(query?, kinds?, limit?)` | Lessons, strategies, fear warnings and recent episodes, with strength, stability, evidence and provenance. With `query`, the most similar come first. Inspecting doesn't count as a recall, so it doesn't change learning. |
| `delete_memory(memory_id, reason)` | Takes a wrong or harmful memory out of retrieval, after the user confirms. Episodes are archived (never deleted); lessons and strategies are deleted. Links are removed and a snapshot is logged. Fear lessons are refused (use `clear_fear`). |
| `clear_fear(lesson_id)` | Human only. The user confirms and gives their name, which is recorded. Without a client that can ask the user, it refuses and points to `horizon clear-fear`. |

**Approvals use MCP user-input requests.** With protocol ≤ 2025-11-25 the server asks mid-call (`elicitation/create`). With 2026-07-28 the tool returns an `InputRequiredResult`, and the client asks the user and retries. The round-1 work isn't repeated: it travels in an HMAC-signed `request_state` (set `HORIZON_STATE_SECRET` when running several server processes). Horizon asks the user for:
- **a high-stakes close call still tied after the consequence checks** (`ask_human`): the user picks the option, and it becomes the decision (`human_choice`, logged with the approver);
- **a rollback escalation:** the user's guidance resumes the task;
- **deleting a memory:** confirm;
- **clearing a fear:** confirm and give a name.

Every answer, including decline, cancel and "client can't ask", is logged. The count is in `horizon stats` → `approvals` (§14: the approval rate should fall). Seen with real Claude Code (2.1.283): it declares support for user-input requests. In headless `claude -p` there is no human, so the request comes back cancelled and nothing changes, even if the prompt claims the user already agreed.

## Hosted server: accounts, credits and the account page (Phase 7)

`horizon serve --transport http` serves MCP at `/mcp` and the account page at `/`.
- **Keys:** operators create a team with `horizon create-team NAME`, which prints the first key and grants 1,000 free starter credits, and top it up with `horizon add-credits TEAM N`. There are no payments yet.
- **MCP auth:** a team key `hzn_…` (Bearer or `X-API-Key`). Each team's tasks and memories are isolated. `HORIZON_DEV_API_KEY` still works as an unmetered local key.
- **Credits:** each call is checked against and charged to the team's balance. Current prices: 1 for `start_task`, `recall_context` and `record_outcome`; 5 for `evaluate_options` and `submit_consequences`; inspection and approvals are free. At zero the tool returns an error, and the host carries on without Horizon.
- **Account page:** sign in with a key. It shows credits, keys (create, with the key shown once, and revoke), usage for the last 30 days, the credit history, and **savings per session**. Savings are counted events only: decisions scored, close calls settled from memory, options ruled out before implementing, regressions caught by a rollback, fear warnings, and human approvals, plus tokens saved where past spikes reported their token cost.
- **JSON API:** `GET /api/account`, `POST /api/keys`, `DELETE /api/keys/{id}`, with a Bearer team key.

## Less common options

- **`recall_context(checkpoint_commit=…)`:** for hosts without Horizon's hooks, the git commit to roll back to if this step fails.
- **`record_outcome(subagent=…)`:** the subagent that did the work, recorded in the memory's provenance as `client:subagent`.
- **`HORIZON_OTHER_PROJECT_FACTOR`** (0.85): how much memories from other projects in the team count. 0 makes projects hard walls.
- **A severe outcome** (`severity: "severe"`) creates a fear lesson and escalates to the user at once.
- **Operator commands:** `horizon consolidate`, `horizon clear-fear ID --by NAME`, `horizon purge-memory ID --reason … --by … --yes` (hard erasure of one episode, e.g. for a legal request; exports to rebuild are listed), `horizon export-decisions`, `horizon compare-scorers`, `horizon eval-world-model`, `horizon stats`.

## Memory that learns (`MEMROUTER.md`, Phase 6)

- **Recall** (`recall_context`):
  - Candidates are episodes, lessons and strategies, ranked by similarity × condition match × scope × strength.
  - Activation spreads two hops through learned links, and the top 25 are shortlisted.
  - Jev (when `HORIZON_SCORER=jev`) keeps the memories it judges relevant, within the token budget.
  - **Fear lessons always surface** when their conditions don't rule them out.
  - Each item carries its `kind`, `strength`, `evidence` and `activation`.
- **Learning** (`record_outcome`): the memories about the option the host implemented learn from the outcome's surprise (lr × surprise × signal weight):
  - their links to each other and to the new episode change;
  - their strength moves the same way;
  - a helpful recall also grows their stability, so later decay is slower;
  - recalled memories about other options lose a little strength.
- **Sleep job:** runs every `HORIZON_CONSOLIDATION_EVERY` episodes (200), or with `horizon consolidate` (for example, nightly from cron). It:
  - turns 3+ agreeing episodes into a strategy (≥ 80 % success) or a lesson (≤ 30 %), and merges duplicates;
  - archives older evidence to `episodes_archive` (never deleted);
  - prunes links below 0.05;
  - writes a Parquet export of all episodes to `HORIZON_EXPORT_DIR` (needs the `[export]` extra).
- **Reconsolidation:** a contradicted lesson first gets narrower conditions (e.g. `rps <= 3000`). It is weakened and linked `contradicts` only when same-condition contradictions repeat.
- **Fear memories:** a `severity: "severe"` outcome creates one at once. Contrary evidence weakens it visibly. Only a human clears it: `horizon clear-fear <lesson_id> --by <name>`.
- **Predictor trust:** calibration, accuracy and Brier score per prediction source and task type (a `task_type` condition), in `horizon stats`.

## Rollback rules (`PROJECT.md` §8)

**Baseline first (`PROJECT.md` §9).** After `start_task`, the host runs the full test suite once, before any edit. The PostToolUse hook captures the failing test ids. The first `recall_context` (or a `record_outcome` made before it) takes those runs as the task's **baseline**. Every later outcome is judged against it, and the result is reported in `test_judgement`:
- **Pre-existing failures** (failing at the baseline and not a target test) are excluded from the success rate and listed separately.
- **Target tests** (`start_task`'s `target_tests`, plus test files named in the goal) always count.
- **Regressions** (failing now, not failing at the baseline) are what trigger a rollback.
- **Absolute fallback:** with no baseline run, or when the output didn't name every failure (for example `pytest | tail -1`), the rule is the pass rate against `HORIZON_ROLLBACK_BELOW`. `test_judgement.method` is then `"absolute"`.

When an outcome fails by those rules, the response carries `rollback`:

- **`action: "rollback"`** (attempts 1 to N−1): `restore.git` is a command that puts tracked files back to the last known-good checkpoint (`git restore --source=<snapshot> --staged --worktree -- :/`). HEAD and history are untouched, and untracked files are left alone. `restore.claude_code` names the prompt to pick in `/rewind`, which only the user can run. The host restores, then calls `recall_context`. That response's `task_state.retry` lists every failed approach and its reason, so the retry doesn't repeat one.
- **`action: "escalate"`** (attempt N): the task becomes `escalated`. The host restores, stops and asks the user. Calling `recall_context` with their answer as `human_guidance` resumes the task with a fresh streak.
- Every attempt in a streak rolls back to the checkpoint taken before the **first** failure, so a host that skipped a restore can't make a broken tree the new baseline. A passing outcome ends the streak.

## Privacy

Decision text written by the host (`situation`, `chosen`, `alternatives`, `reason`, progress notes, open issues, plan, constraints) is redacted before storage. Code blocks and code-like lines are removed, and each field is capped in length (`src/horizon/redact.py`). The task goal is kept verbatim (`PROJECT.md` §8). Test results are stored as counts only.

## Measuring whether the host calls us

`demo/reliability/` runs 4 small tasks as real Claude Code sessions and reports how often each tool was called at the right moment. See its README.

`horizon stats` prints tool call counts and errors, tasks by status (including `escalated`), `outcome_recording_rate`, and the Phase 3 checkpoint counts (`checkpoints_captured`, `checkpoints_attached`). That rate is the share of real test runs (captured by the hook) that were followed by a `record_outcome` call. It is the Phase 2 reliability metric (`PROJECT.md` §14: "rate of reliable MCP invocation by the host").
