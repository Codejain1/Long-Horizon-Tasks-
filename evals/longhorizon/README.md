# Long-horizon A/B evaluation

Does Horizon help where it's meant to: across sessions, with constraints stated once, and with early decisions that bite later? Each scenario is a small project built over **4 fresh Claude Code sessions**. Every session gets only a short "continue the project: …" prompt, like coming back to work the next day. Both arms run the same model, prompts, permissions and isolation. The only difference is whether Horizon is installed.

| Scenario | What it tests |
|---|---|
| `ledger` | **Constraint drift.** Session 1 says: integer cents only, stdlib only, never rename public functions, Python 3.9. Sessions 2–4 (persistence, currencies, budgets and a CLI) never repeat it, and session 3 tempts floats. |
| `notes` | **An early decision that bites later.** Session 1 designs a note store; session 3 must survive 8 processes writing at once without losing a note; session 4 adds edits and deletes under concurrency. |
| `team`, `backlog` | Team rules carried to new projects (3 repos); unattended tickets under CI policies (8 sessions). |
| `marathon` | **The v2 Phase 3 gate** (`docs/EVALS.md`). 24 unattended tickets on an invoicing codebase of about 400 lines with 5 CI policies found only by running `./ci.sh`, CI in `Asia/Kolkata`, tests run in reverse order, a rule stated once in ticket 1 (integer cents, `uuid4().hex` ids), ticket 4's storage choice tested by 8 processes in ticket 16, a backup migration (12) and a dependency temptation (18). |
| `compaction` | **Re-anchoring.** One session over three invocations: the goal, 4 constraints and parts 1–3; then `/compact`; then parts 4–8, which don't restate the constraints and each offer a shortcut (floats, pandas, local time, a rename). |
| `stall` | **Stall detection.** Six tickets on the marathon codebase; #3 (output the platform's policy forbids) and #5 (a binary only CI has) can't be done, and the right result is `BLOCKED.md` with the reason. Faking them is caught. |

**Arms:** `baseline` (plain Claude Code), `observe` (plain Claude Code plus Horizon's silent event recorder), `lean` (the default install), `candidate` (lean with the features still behind the gate turned on; now `HORIZON_SESSION_MEMORY=on`, past sessions as context, and `HORIZON_CONTEXT=composer`, the context composer; `--set KEY=VALUE` adds or overrides a setting for ablations, and `results.json` records the arm's settings) and `horizon` (the full workflow).

After every session, **outside the repo, where the agent can't see them**:
- the hidden acceptance tests of every session so far (`scenarios/*/hidden/`);
- the session-1 constraints (`checks.py`): the imports are stdlib only, there's no syntax newer than 3.9, and the original public functions still exist with the same leading parameters.

The repo is committed between sessions. `reference/` holds solutions that pass every hidden test, which proves the tests are fair. A naive JSON store is shown to fail the concurrency test, which proves that test discriminates.

## Run

```bash
python evals/longhorizon/run.py --scenario ledger --arm baseline --out /tmp/lh
python evals/longhorizon/run.py --scenario ledger --arm horizon  --out /tmp/lh
python evals/longhorizon/report.py /tmp/lh
```

- Each run uses 4 Claude Code sessions on your login. Run the two arms of a scenario at the same time, so they see the same conditions.
- The Horizon arm uses `HORIZON_SCORER` and keys from the environment.
- Sessions cut short by a usage limit are marked `aborted` and end that run.
- `--resume RUN_DIR --from-session N` starts from another run's repo after session N−1. `--seed RUN_DIR` first replays another run's sessions through the installed hooks (use it for `stall`, so Horizon has normal work to compare against).
- Arms with Horizon installed leave `events.jsonl`, the run's whole event log (world-model training data).

## Reading the results

- **Per session:** hidden tests passed and failed, constraint violations, turns, tool calls, Horizon calls, and the cost-equivalent Claude Code reports.
- Also, from the transcript and the hidden tests rather than the agent's summary: the checks that failed in the agent's own test runs, **repeat failures** (a check failing again after an earlier session saw it fail), **regressions** (a hidden test that passed after the previous session and doesn't now), and for Horizon arms the session's events and the characters Horizon injected. `compaction` rows list each invocation and count compactions; `stall` rows record whether `BLOCKED.md` was written and gives the right reason.
- **The questions:** does the Horizon arm keep the constraints better, avoid rework in session 3, finish with more hidden tests passing, and at what extra cost in turns and tokens?
- **Caveat:** one run per arm is a first signal, not a statistical result. Repeat before believing a difference.
