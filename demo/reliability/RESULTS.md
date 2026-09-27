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
