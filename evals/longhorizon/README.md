# Long-horizon A/B evaluation

Does Horizon help where it's meant to: across sessions, with constraints stated once, and with early decisions that bite later? Each scenario is a small project built over **4 fresh Claude Code sessions**. Every session gets only a short "continue the project: …" prompt, like coming back to work the next day. Both arms run the same model, prompts, permissions and isolation. The only difference is whether Horizon is installed.

| Scenario | What it tests |
|---|---|
| `ledger` | **Constraint drift.** Session 1 says: integer cents only, stdlib only, never rename public functions, Python 3.9. Sessions 2–4 (persistence, currencies, budgets and a CLI) never repeat it, and session 3 tempts floats. |
| `notes` | **An early decision that bites later.** Session 1 designs a note store; session 3 must survive 8 processes writing at once without losing a note; session 4 adds edits and deletes under concurrency. |

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

## Reading the results

- **Per session:** hidden tests passed and failed, constraint violations, turns, tool calls, Horizon calls, and the cost-equivalent Claude Code reports.
- **The questions:** does the Horizon arm keep the constraints better, avoid rework in session 3, finish with more hidden tests passing, and at what extra cost in turns and tokens?
- **Caveat:** one run per arm is a first signal, not a statistical result. Repeat before believing a difference.
