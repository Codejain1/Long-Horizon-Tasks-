# Ideas harvested from Horizon v1/v2

Before the old code was deleted (2026-10-18), it was searched for what the current system (memrouter + runner +
Hermes) can reuse. The code is still in the tag: `git show snapshot-pre-restructure:<path>` or
`git checkout snapshot-pre-restructure -- <path>`. The results quoted are from `evals/longhorizon/RESULTS.md` and
`docs/MEMORY_BENCHMARKS.md` in the tag (Claude Code + Sonnet, mostly 1–3 runs each).

## Use soon (evidence that it worked, and it fits a current gap)

| # | Idea | Evidence | How it applies now | Old code | Effort |
|---|---|---|---|---|---|
| 1 | **Recurring-failure warnings.** Name the exact check that failed in 2+ earlier jobs ("get `test_x` right the first time") instead of raw error text | Backlog replays: 0/5 vs 3/5 and 0/5 vs 2/5 sessions breaking the policy, −17 % and −18 % cost. The best memory result we have | Replace the runner's raw-pytest "advice" notes with "these checks failed in N past jobs of this kind". The hidden-test feedback already names the failing checks | `src/horizon/composer.py` (pitfalls section), `hooks.py` (`recurring_failures`), `taskstate/store.py` | Small |
| 2 | **Notes-file and MCP-memory arms.** The baselines that could beat memory | A notes file the agent was told to keep tied Horizon (20/20 rules, $3.04 vs $3.07) within a project. It lost across projects (2/4 vs 4/4) | Add a `notes` arm to `bench/bench.py`: the owner's feedback appended to a file that is pasted into each job. Run it against memrouter on the house set | `evals/longhorizon/run.py` (`notes`, `mcpmem` arms), `score_h2h.py` | Small |
| 3 | **Cost to working software.** Keep giving fix-up rounds with the failing checks until the hidden tests pass, and count the rounds, time and cost | Ward and rental: memory −15 % cost, −16 % time, 5 vs 12 rounds, 1 vs 10 checks still failing | A better benchmark metric than solved yes/no: it captures what forgetting really costs. Also a natural owner-feedback loop for Hermes jobs | `evals/longhorizon/fixup.py` | Medium |
| 4 | **Calibrated scores.** Platt scaling for the ranker, isotonic per question family for the judge, so a 0.8 means 80 % | Built and measured (Brier, ECE, reliability table); OwnJudge built and tested, never used | Fixes memrouter's relative scores (`MIN_SCORE 0.5` means nothing today). The planned OwnJudge calibration from runner outcomes is this code | `evals/memory/calibrate.py`, `src/horizon/decision/scorers.py` (`OwnJudge`) | Small–medium |
| 5 | **Real multi-ticket scenarios** with hidden tests and rule checks: ward (14 tickets, 11 rule checks), rental (12 tickets), team (3 projects), backlog (CI policies), marathon (24 tickets), handoff | They separated arms clearly (rules 40/40 vs 7/40). Each has a reference solution and hidden tests | Truly long-horizon work for the runner, instead of 40-line single-file jobs | `evals/longhorizon/scenarios/`, `reference/`, `checks.py` | Medium (adapt to the runner) |
| 6 | **Project map at start.** Modules, public functions with signatures and docstring first lines, and how tests run | −1 % cost, 7 % faster at equal rules. About half of a session's tokens went on reading before the first edit | When the runner works on an existing repo, or a job grows past one file | `src/horizon/projectmap.py` (89 lines) | Small |
| 7 | **Judge against a baseline.** Tests already failing at the start are excluded; only regressions trigger rollback | Built and tested (v1 phase 3) | Needed once the runner works on existing repos | `src/horizon/taskstate/judge.py`, `rollback.py`, `checkpoints.py` | Small |
| 8 | **Real token accounting** per attempt | Built (v1); vendors report tokens per task | The runner logs minutes, not tokens. vLLM's `usage` field gives the tokens for free | `src/horizon/taskstate/usage.py` (the idea; the code reads Claude transcripts) | Tiny |
| 9 | **Rule-detection test set.** 69 labelled sentences (team rule, project rule or neither) | Keywords 62 % vs Jev 97 % on 32; Jev 85 % on 54 ticket-style sentences | A test for memrouter's rule capture (`_RULE` regex + writer), which today has only a few hand-picked cases | `evals/rules/labelled.json`, `run.py`, `tickets.py` | Tiny |

## Keep as ideas (built, no gain shown yet, may matter later)

- **Refused-command memory** (`src/horizon/deadends.py`): warns before a command refused twice is retried. Refused
  calls fell 13→8 and 8→2, but no token saving. The runner has the within-job version (`failed_cmds`).
- **Session forecast / world model v2** (`src/horizon/eventlog/forecast.py`, `decision/worldmodel.py`): from similar
  past jobs, predicts the chance of success, failing runs (mean, p90) and tokens, shrunk toward a base rate, with a
  replay gate. It never passed its gate. It's the starting point for "stop doomed jobs early".
- **Context composer** (`src/horizon/composer.py`): packs context by kind within a budget, least essential dropped
  first, each line once, and adds goal-dependent notes only when trouble is forecast. Useful if the runner's
  prompts get crowded.
- **Outcome judge without tests** (`src/horizon/eventlog/judge.py`): five ordered levels from "the user says it
  failed" to "confirmed", plus a pushback check on follow-ups. For Hermes tasks with no tests (orders, bookings).
- **Redaction** (`src/horizon/redact.py`): strips code blocks and code-like lines and caps length before storing.
  It would have kept raw pytest tails out of runner notes.
- **Selective forgetting** (`src/horizon/factmem.py`, FactConsolidation runner): everyone scores ≤ 7 % on
  multi-hop in the paper. Our 68 % vs full history's 96 % shows the gap still open.
- **Decision layer** (`src/horizon/decision/`): it never changed an outcome on Sonnet. On Haiku, Jev steered
  storage to SQLite with transactions, the only arm whose bookings survived. Worth it only where the agent can't see
  the answer but memory can.

## Facts learned (don't re-learn them)

- Within one project, a notes file the agent is told to keep matches automatic memory on rules at the same cost.
  Automatic capture and crossing projects or people are the real edges.
- Stall warnings, re-anchoring after compaction, past-session briefings and spreading activation showed no gain
  in their gates.
- Memory benchmarks (Codex as judge, shared questions): LoCoMo +27.4 pts vs plain retrieval (p < 0.001) but
  −9.7 vs full history. BEAM 100K and LongMemEval-S are not significant. FactConsolidation multi-hop is −28 vs full
  history.
- Benchmark hygiene that mattered: hidden tests the agent never sees, scoring only questions every arm answered,
  an independent judge, paired intervals, and checking flaky hidden tests (a flaky concurrency test once reversed a
  verdict).
