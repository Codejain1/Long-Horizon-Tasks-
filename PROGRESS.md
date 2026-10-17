# Progress

The full log up to 2026-10-18 (1,300 lines: Horizon v1/v2, memory benchmarks, Hermes setup) is in the git tag
`snapshot-pre-restructure` (`git show snapshot-pre-restructure:PROGRESS.md`).

## Status (2026-10-18)

**Live**
- **Hermes** on AWS (Mumbai `i-0d0e2f600c143e705`). It runs on WhatsApp (self-chat only), with memrouter as its
  memory (`/opt/embed-memory`) and the rails plugin.
- **The model:** Qwen3-14B-AWQ on vLLM on a Stockholm g5.xlarge ($1.07/h while on), reached through an SSH tunnel.
- **Claude Code memory:** memrouter over MCP (`claude/`).

**Results that hold**
- **Recall engine:** on LoCoMo it beats plain retrieval (the only significant memory-benchmark result). Search takes
  25 ms, episode writes 21 ms, and `/add` through the Qwen writer 2.7 s (in the background).
- **Rules:** an owner rule ("from now on…") becomes a standing rule that reaches every runner job.
- **Runner verification:** when the runner says a job is done, the hidden tests agree 63/64 times
  (fastbench2 + fastbench3).

**Results that don't prove what they seemed to**
- **Conventions run (housebench):** late-half house-rule checks +64.6 pts [+43.8, +83.3] with memory vs none. But the
  bench hands memory the exact rule sentence and the none arm gets no feedback, so it shows "told the rules" beats
  "never told". A notes file would likely match it.
- **Experience lessons on ordinary tasks:** no benefit. Run 2: +0.0 [−18.8, +15.6]. fastbench3, with silent memory:
  −6.2 [−21.9, +9.4]. The none arm alone moved 78.1 → 71.9 % on identical tasks, so ±6 pts is noise. What gets
  through the filter is raw-pytest "advice" notes.
- **The relevance bar:** memrouter scores are relative (per-query normalised, LoCoMo-trained), so `MIN_SCORE 0.5`
  does not mean "relevant".

**The runner's real cost**
- About half the jobs end on the 10-min budget, and 31 of those had already passed the hidden tests.
- Contract, tests and spec review take 56–62 % of the time on ~40-line programs.

## Architecture review (2026-10-18)
Evidence from the code and from the live Hermes memory (read-only):
- **Experience isn't wired to Hermes:** 0 episodes and 0 credited recalls in 214 memories since 5 Sep. Only the
  benchmark records outcomes.
- **Wrong standing rules are injected into every reply:** "The assistant can find Japanese, Mexican or Italian
  dessert options" and 3 similar capability statements are stored as rules. A profile entry is a question ("which is
  better, paddu or idli").
- **The writer drops numbers or names in 35 of 59 writes** (`checked = mismatch`).
- **Credit uses the runner's own step results:** the hidden-test outcome is sent without a `recall_id`, so it never
  reaches the memories that were recalled.
- **Consolidated "strategies" are the retry label** ("build via first try succeeded in 5/6"), with no content.
- **One SQLite connection shared across server threads,** with reads outside the lock (the flaky test).
- **The memory service also does geocoding, food search, mood and location.**
- **Hermes, the writer and every benchmark stream share one A10G.**

## Long-horizon parts 1-5 (2026-10-18, design in docs/LONG_HORIZON.md)
1. **Design doc:** written.
2. **Judge (`runner/judge.py`):** built.
   - Backfill over 250 past jobs, job_done verdicts held out: Brier raw 0.323 (worse than the base rate 0.250, ECE
     0.32: overconfident), calibrated 0.220, ECE 0.004. **The acceptance criterion is met.**
   - Benchmarks now label every job's verdict with its hidden result.
3. **Research jobs:** `--kind research`, the evidence check (each quote must be on its page) and `bench/web.py`
   (versions / places / flight prep, scored against independent truth). Live run `/home/hermes/web1` (plain vs
   runner, 2 reps) has started; results pending.
4. **Big-model arm:** per-role endpoints (`--planner/--spec/--judge URL,MODEL,KEY_ENV`, `--api-key-env`), tokens
   per model, backlog `--label/--extra`, retries on rate limits.
   - **Owner OK'd the free OpenRouter model.** Running in `/home/hermes/backlog-smoke-e` next to plain / runner / mem
     (rental tickets 1–3): `big` = nvidia/nemotron-3-ultra-550b-a55b:free alone (plain loop), `bigplan` = runner +
     memory with that model as planner only.
   - The judge stays on local Qwen in every arm.
5. **World model (`runner/forecast.py`):** trained on 250 real jobs (fastbench2/3, housebench, housebench2).
   - Grouped replay: Brier 0.235 vs base rate 0.250, gain +0.0145 [+0.004, +0.025]. **The gate passes.**
   - The doom policy (stop if P < 0.1 after 40 % of the budget) never fires: the model is never that sure, so it
     saves nothing yet.
   - Model at `/home/hermes/forecast/forecast.json`.
- **Research live run web1 (invalid):**
  - the planner used `true` checks;
  - Qwen wrote versions from memory (Python 3.11.6 vs the true 3.15.0);
  - the judge accepted them from the doer's summary;
  - the goal never told the plain arm the answer format.

  Fixed: trivial checks become evidence checks, a job finishes only once every claim is on its page, the judge
  treats the summary as a claim, and every goal states the answer format. Rerun as `/home/hermes/web2`.
- **Brownfield:** the smoke showed every arm failed rental ticket 1 by rebuilding the project. The runner now has a
  project map, existing tests fixed from the start and re-run, writes that drop names or shadow the package refused,
  and an extend-don't-rebuild plan shape. The guard was seen working live (it refused a rewrite that dropped
  `KINDS`). The v3e smoke `/home/hermes/backlog-smoke-e` (rental 1-3, plain / runner / mem) is running.
  - **Ticket 1, runner:** it extended `properties.py` correctly but forgot `from typing import Optional`
    (NameError) and ran out of its 12-minute budget during the re-plan.
  - **Ticket 1, plain:** it wrote the function, ran only the 3 old tests, stopped, and never exported it.
  - These are now model-quality failures, not harness bugs.

## In progress
- **Wrong live rules removed (2026-10-18, owner's instruction):** rules 3–6 and profile 5 are deactivated; live
  Hermes now has 1 rule and 4 taste entries. The backup is `memory.db.bak-20261017-203609`.
- **Step 0 (stop the harm):**
  - **Concurrency fix (done):** every SQLite statement runs under one lock with its rows fetched whole, and the
    HTTP accept queue went from 5 to 128. A new stress test (8 threads × 60 requests) passes; the old flaky test
    passed 10/10.
  - **Wrong live rules (not done):** removing rules 3–6 and profile 5 on live Hermes was approved by the owner but
    blocked by the permission check. The owner runs it (command in the session summary). A backup exists:
    `/home/hermes/.embed-memory/memory.db.bak-20261017-203609`.
- **Step 1, measurement (built, smoke running):**
  - `bench/backlog.py` runs the rental and ward multi-ticket scenarios in one repo, with hidden tests after each
    ticket and QA fix-up rounds.
  - Arms: plain / runner / notes / mem. The runner gained `--plain` and token accounting.
  - The scenarios are checked: rental's 23 and ward's 31 hidden tests pass on the reference and fail on the start.
  - Smoke runs (rental tickets 1–2): `/home/hermes/backlog-smoke` (v3a = the system before the redesign) and
    `backlog-smoke-b` (v3b = after). Check with `bash bench/backlog_progress.sh backlog-smoke backlog-smoke-b [report]`.
- **Step 2, memory redesign (built):**
  - Directives inferred by a model wait as pending; Hermes asks the owner (`confirm_rule`, `confirm_preference`).
  - `failures.py` replaces `experience.py`: failing checks from real outcomes, per scope, become warnings, credited by
    `warning_id`.
  - The runner reads rules, warnings and what the owner said, and writes nothing back.
  - Not yet deployed to live Hermes. The food/location move waits for that deploy.
- **Step 3, lean runner (built):** it finishes when every fixed test passes, reviews the spec only when stuck, and
  thinks only on escalation.
- **housebench2 (stopped at ~60 %, old memory design):**
  - mem vs none solved +38.9 [+16.7, +61.1], house checks +42.6 [+20.4, +64.8];
  - rules-only vs none solved +15.8 [+0.0, +31.6], house checks +33.3 [+17.5, +49.1].
  - Rules alone carry most of the rule-following. Full memory solved more, likely via the feedback naming failing
    checks, which is what `failures.py` now does on purpose.

## Decisions made
- 2026-10-18: repo restructured to only what runs. Everything else is in the tag `snapshot-pre-restructure`;
  `IDEAS.md` lists what is worth reusing from it, with evidence and paths.
- 2026-10-18: the runner no longer writes Horizon events (`--events-db` removed): nothing read them.
- 2026-10-18: `stall.py` trimmed to `Run` + `stuck`, the only parts the runner uses.
- 2026-10-18: LoCoMo loading for training is now `memrouter/locomo_data.py`. Training scripts use fastembed
  directly instead of `horizon.memrouter.embedding`.
- 2026-10-17: runner lessons are silent by default: rules always; beyond them only advice, lessons and fears with
  score ≥ 0.5, at most 3.

## Open questions
- **Flaky test:** `test_experience_loop_through_memrouter` fails about 1 in 5 full-suite runs, never alone. It's
  likely concurrent memory writes on one SQLite connection. Default: leave as is until it bites.
- **Stockholm GPU billing** ($1.07/h): default is to keep it on while housebench2 runs.

## Next step (plan agreed for review, 2026-10-18)
0. **Stop the harm (≤ 1 day):**
   - remove the 4 wrong live rules and the wrong profile entry (owner's yes needed);
   - fix SQLite concurrency (connection per request + WAL);
   - finish housebench2.
1. **Measurement loop (largest, ~4–5 days):**
   - a real benchmark: the ward and rental multi-ticket projects adapted to the runner;
   - the metric is cost to working software (fix-up rounds until the hidden tests pass);
   - arms: plain Qwen loop, runner, runner + notes file, runner + memrouter;
   - benchmarks run only when Hermes is idle.
2. **Memory redesign (~4 days):**
   - directives: owner-confirmed, scoped, revocable;
   - experience: keyed by failure signature, written only from ground truth with the recall link, shown as
     concrete warnings;
   - conversation recall unchanged;
   - food and location move into their own Hermes tool.
3. **Lean runner (~2 days):** code and tests written together; stop when the deliverable passes; spec review only
   when stuck.
4. **Hermes outcomes (~1 day, after 2):** owner accepts or corrects a reply → an experience record.
5. **Model and compute only where 1 shows a need:** a stronger writer or spec model, or a second GPU for benchmark
   days.
