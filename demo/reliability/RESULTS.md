# Reliability results

Measured on 2026-09-28 with Claude Code 2.1.283, headless (`claude -p`), on a local Mac. Five tasks per run, each in a fresh session. The prompts never mention Horizon.

| Run | Host model | Horizon version | start before edit | recall before edit | record after last test | `task_complete` | hooks fired | restored after rollback | recalled after rollback | tests pass |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Opus 5.5 | Phase 3 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 1/1 | 1/1 | 5/5 |
| 2 | Sonnet 5 | Phase 3 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 1/1 | 1/1 | 5/5 |
| 3 | Opus 5.5 | Phase 3 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | **0/1** | 1/1 | 5/5 |
| 4 | Opus 5.5 | + restore fix | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 1/1 | 1/1 | 5/5 |
| 5 | Sonnet 5 | + restore fix | 5/5 | 5/5 | 5/5 | **4/5** | 5/5 | 1/1 | 1/1 | 5/5 |
| 6 | Sonnet 5 | + allowlist fix | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 1/1 | 1/1 | 5/5 |

**Runs 7–10: after the baseline and zero-centred surprise changes.** Rollbacks now trigger only on regressions, and task 5 was redesigned so its first attempt breaks `sentence_case`. A new column, `baseline_run`, records whether a test run happened after `start_task` and before the first `recall_context`.

| Run | Host model | Horizon version | start before edit | baseline run | recall before edit | record after last test | `task_complete` | hooks fired | restored after rollback | tests pass |
|---|---|---|---|---|---|---|---|---|---|---|
| 7 | Opus 5.5 | baseline v1 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 1/1 | 5/5 |
| 8 | Sonnet 5 | baseline v1 | 5/5 | 4/5 | 5/5 | 5/5 | 5/5 | 5/5 | **0/1** (4 false rollbacks) | 5/5 |
| 9 | Opus 5.5 | + baseline ordering fix | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 1/1 | 5/5 |
| 10 | Sonnet 5 | + baseline ordering fix | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 1/1 | 5/5 |

- **Run 8.** Sonnet ran the baseline suite as told, but then called `record_outcome` on that run **before** its first `recall_context`. That consumed the baseline as an outcome, so the absolute fallback fired a rollback before any edit. **Fix:** test runs before the first recall are the baseline, whichever tool comes first. A `record_outcome` on the baseline run is acknowledged (`recorded: false`) and nothing is judged.
- **Runs 9–10.** Every outcome was judged with the baseline method. Each task excluded 5–11 pre-existing failures (the other tasks' unfinished stubs), and there was exactly one rollback per run, on the real 2-test regression. It was restored.

`outcome_recording_rate` (hook-captured test runs later used by `record_outcome`) was 1.0 in every run.

**The two misses:**
- **Run 3, restore skipped.** After the rollback, Opus judged the restore unnecessary because it was about to rewrite the same line. It went straight to `recall_context` and then an edit, so the retry didn't start from the checkpoint as `PROJECT.md` §8 requires. There were two fixes:
  - The rollback `next` text and the CLAUDE.md snippet now say to restore "right away, before any other edit, even if you will rewrite the same code".
  - The PreToolUse hook now checks that the tree matches the rollback target and denies the recall once if it doesn't.

  In runs 4–6 both models restored on their own, so the hook never had to deny.
- **Run 5, no `record_outcome`.** Sonnet ran `python3 -m pytest`, which the demo's permission allowlist didn't cover. The tests never ran, so there was nothing to record, and the Stop hook correctly stayed quiet. This was a harness problem, not a Horizon one. The allowlist now includes `python3`, and the report ignores test commands that were denied. Run 6 was clean.

**Caveats:** these are small single-file tasks with one prompt each. Real multi-hour sessions, interactive use (where the user approves each tool) and other hosts aren't covered. The numbers show the wiring works and the instructions are followed, not that this holds at scale.

## Phase 4: `evaluate_options` at a crucial choice (2026-09-28)

Task 6 only, on Opus 5.5 and on Sonnet 5, with no scorer configured (`HORIZON_SCORER=none`). The prompt asks for a persistent cache that "will later be shared by several worker processes" and never mentions Horizon.

| Host model | `evaluate_options` before the first edit | options passed | baseline run | recall before edit | record after last test | hooks fired | tests pass |
|---|---|---|---|---|---|---|---|
| Opus 5.5 | yes (1 call) | SQLite (WAL), shelve/dbm, JSON + atomic rename, Redis | yes | yes | yes | yes | yes |
| Sonnet 5 | yes (1 call) | sqlite3, JSON + locking, dbm/shelve, Redis | yes | yes | yes | yes | yes |

**Finding and fix.** With no scorer, the first version returned the cheapest option by the host's own estimates as `chosen` (shelve/dbm), and shelve can't serve several processes. Both hosts ignored it and built sqlite3 ("I'll decide based on engineering merit rather than the token-cost ranking"). `unscored` now returns `chosen: null`, and the estimate ranking comes back separately as `cheapest_by_estimates`. Estimates show what's cheap, not what works.

Not measured yet: real Jev or LLM scores (no API keys). With a scorer, the same run would also test whether hosts follow `clear_winner`, `close_call` and `ask_human`.

### With live Jev (`HORIZON_SCORER=jev`, `jev-1.13.0`)

| Host model | `evaluate_options` before edit | Jev decision | margin | host followed it | tests pass | episode prediction |
|---|---|---|---|---|---|---|
| Opus 5.5 | yes | `clear_winner`: stdlib sqlite3 (WAL), 0.91 vs next best 0.41 | 0.50 | yes | yes | 0.9 from `host` (before the fix below) |
| Sonnet 5 | yes | `clear_winner`: sqlite3, 0.87 vs diskcache 0.46 | 0.41 | yes | yes | 0.9 from `host` (before the fix) |
| Sonnet 5 (rerun) | yes | `clear_winner`: sqlite3 | — | yes | yes | **0.87 from `jev`**, surprise +0.13 |

- Each decision is one Jev request of 4 + 4 × options questions, taking 0.45–0.71 s. Redis ranked near zero under a "stdlib only" constraint, and shelve/dbm (no safe concurrent writers) ranked low.
- **Fix: prediction source.** Hosts pass their own `predicted_success` as well, and that used to win. Now, when the implemented option is the one the scorer evaluated, the scorer's prediction is recorded (§9 "record what Jev predicted"), so Phase 6 predictor trust measures Jev.
- **Fix: high-stakes wording.** "Delete or overwrite data" fired on a cache (Noul 0.80). The new wording ("real-world harm that is hard to undo: … destroying existing user or production data", with caches, temp files, build output and git-undoable changes excluded) scored 0.20 on the cache. True positives stayed high: a production table migration 0.93, emailing 40k customers 0.93, $3k/month of cloud spend 0.87.

## Phase 5: consequence checking with a real host (Sonnet 5, live Jev)

Task 6 again, with a close call forced through existing settings (`HORIZON_CLEAR_MARGIN=1.0`, `HORIZON_TRY_REVERSIBLE_THRESHOLD=1.1`). Jev on its own rarely produced ties: three hand-picked library choices were all clear winners, with margins of 0.17–0.24.

| Run | `check_consequences` | spikes built | `submit_consequences` | final | permission denials | working tree after |
|---|---|---|---|---|---|---|
| 1 | yes, 4 spikes | real multi-process stress tests (20 concurrent writers) in `/tmp` | 1 call: dbm "silently lost 28/360 writes" (`passed: false`) | sqlite3; dbm/shelve and diskcache eliminated | **10** (paths outside the project) | only `textkit/cache.py` |
| 2 | yes | under `.horizon/spikes/`, deleted afterwards | 2 calls (the first was rejected, see below) | `clear_winner` sqlite3; the other two eliminated | 1 | only `textkit/cache.py` |

**Fixes from these runs:**
- **Spikes now go under `.horizon/spikes/`**, which the installer adds to `.gitignore`. Inside the project the host needs no extra permission, and git and the checkpoint snapshots never see the files.
- **Redaction false positive (a Phase 2 bug).** The option label `dbm (stdlib)` came back as `[code removed]`: it's 12 characters, 2 of them parentheses, which is over the 15% symbol ratio. The host's first `submit_consequences` with the real label was then rejected. Now the ratio rule needs at least 3 symbols, and option matching also compares redacted labels.
- **Pass-2 confidence (live Jev, date-parsing pair).** Jev flipped the winner correctly when the spike evidence flipped. But a 0.30 lead after evidence was held back as "still close" because one Score confidence was 0.47. After evidence, a lead of twice the clear margin now stands.
