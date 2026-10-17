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

## In progress
- **housebench2** (`/home/hermes/housebench2`): the house set with mem / rules / none × 2 seeds. It shows how much
  of the conventions win is the rules alone. Check it with `bash bench/progress.sh housebench2 [report]`. The server
  runs the pre-restructure copy in `/opt/longrun/fb4`.
- **Restructure (branch `v3/lean-repo`):** the live code moved to `memrouter/ runner/ bench/ hermes/ claude/`.
  The runner's write-only Horizon event log was removed, and 128 tests pass. With the owner's OK, the old code
  (`src/ evals/ docs/`, Docker files, one-off scripts) was deleted after harvesting what is reusable into `IDEAS.md`.

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

## Next step
1. Report housebench2.
2. Runner baseline: a plain Qwen tool loop vs the runner on the basic set. Also make the runner stop as soon as the
   deliverable passes its own tests.
3. A notes-file arm against memrouter on the house set (`IDEAS.md` #2), and recurring-failure warnings in place of
   raw-pytest advice (#1).
4. Decide what the experience layer stores, based on 2 and 3.
