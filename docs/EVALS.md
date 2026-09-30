# Evaluations: the gate

> v2 principle 5 (`PROJECT.md` §0): **nothing is on by default until it beats plain Claude Code, and the current default, on these evaluations.** The v1 build skipped this and built eight phases before the first measurement; this page is what stops that happening again.

## How a component earns "on by default"

- **Arms:** `baseline` (plain Claude Code), `lean` (the current default) and `candidate` (the build under test). Same model, prompts, permissions and isolation; only the installed profile differs. `observe` (baseline plus a silent event recorder) can stand in for `baseline` when a run should also leave world-model data.
- **Quality must not drop:** on every scenario, hidden tests and constraint/policy checks are at least as good as `lean`'s (allowing one check per repeat of noise).
- **It must win where it's aimed:** each component names the metric it's meant to move (below). No movement on that metric, no default.
- **Cost:** within +10 % of `lean`, unless the quality gain pays for it. Cost is Claude Code's reported cost-equivalent, plus turns and tokens.
- **Repeats:** 3 for a default-on decision. One repeat is a signal, never a decision.
- **Fair tests:** every scenario ships a reference solution that passes all its hidden checks, and a naive one that each check catches (as `evals/longhorizon` does today).

| Component (build phase) | The metric it must move | Scenario |
|---|---|---|
| Episodes from hooks and surprise learning (1–2) | repeat failures, regressions | `marathon` |
| Context composer (1) | the same quality with fewer context tokens | `team`, `marathon` |
| Re-anchoring after compaction (2) | constraint violations after a compaction | `compaction` |
| Stall detection (2) | tokens burned on a stalled task, with no false alarms | `stall` |
| Risky-action foresight (4) | breakage from migrations and dependency changes | `marathon` tickets 12, 18; the weaker-model test |

## Existing suites (v1)

| Suite | Tests | Result so far (`evals/longhorizon/RESULTS.md`) |
|---|---|---|
| `ledger`, `notes` | constraint drift; an early decision that bites later (4 sessions each) | both arms perfect; Horizon only added cost |
| `team` | team rules carried to new projects (3 projects) | 24/24 lean against 15/24 baseline, 3 repeats, +40 % cost |
| `backlog` | unattended tickets under CI policies (8 sessions) | failed-CI sessions 2/8 lean against 6/8; pitfall replays 0/5 against 3/5 and 2/5, 17–18 % cheaper |
| `evals/rules` | picking rules out of prompts (32 labelled sentences) | Jev 97 %, keywords 62 % |

## New scenario 1: `marathon`, the Phase 3 gate

> **Built** (v2 Phase 1, track E): `evals/longhorizon/scenarios/marathon`, a reference solution that passes all 45 hidden checks and the repo's own CI, and `tests/test_marathon.py`, which shows each planted trap caught by its own check. Not yet run with real sessions.

**Question:** over a long, unattended backlog, does Horizon stop an agent repeating failures, breaking earlier work and drifting from rules it was told once, and what does that cost?

**Setup**
- A new starting codebase, not the `backlog` shop, so v2 isn't tuned to one repo: a small invoicing service of about 400 lines (the tickets roughly triple it), with `./ci.sh`.
- **24 tickets, each a fresh `claude -p` session** with the `backlog` preamble ("nobody will answer questions, make reasonable choices and finish the ticket").
- The hidden tests of **every earlier ticket** run after each session, so regressions show up.

**Planted structure (each has its own measure)**

| What's planted | Example | Measured as |
|---|---|---|
| 5 CI policies, found only by running CI | a changelog line per public function; type hints on public functions; no `print` in the library; timezone-aware UTC times; every error carries an error code | **repeat failures:** a check failing in a session after it already failed in an earlier one |
| 2 environment quirks | CI runs with `TZ=Asia/Kolkata`; one test depends on order unless a fixture is used | repeat failures on those checks |
| A rule stated only in ticket 1's prompt | money in integer cents; ids are `uuid4().hex` | **rule drift:** violations in tickets 2–24 |
| An early decision that bites later | ticket 4 chooses the storage; ticket 16 needs 8 concurrent writers | ticket 16's hidden tests, and rework turns |
| Tickets that build on each other | refunds build on invoices, reports on both | **regressions:** an earlier ticket's hidden test newly failing |
| 2 risky actions | a schema migration (ticket 12), a new dependency (ticket 18) | breakage after them (data for Phase 4) |

**Per session we record:** cumulative hidden tests and regressions, policy violations, CI runs and failed CI runs, turns, tokens, cost-equivalent, wall time, and the context tokens Horizon injected.

**Headline:** total repeat failures, regressions and drift violations; tickets complete at the end; total cost.

**Pass (Phase 3):** `candidate` is no worse than `lean` on hidden tests, drift and regressions; strictly better on repeat failures or regressions; total cost within +10 % of `lean`. Otherwise the v2 machinery above lean stays opt-in.

**Budget:** 24 sessions × 3 arms = 72 sessions per repeat. Run 1 repeat first, and 2 more only if it shows a signal.

**Risks:** building the scenario is the biggest piece of work (24 tickets, a reference solution checked after every ticket, a naive solution per policy). Claude Code usage limits cut runs short; `run.py` already resumes from a session (`--resume`, `--from-session`) and marks cut sessions `aborted`.

## New scenario 2: `compaction`, for re-anchoring (Phase 2)

> **Built** (track E): the `tab` package (shared expenses). Parts 4–8 offer a float average, pandas, a local "today", India's days, a rename and `float(rate)`, and each is caught by its own hidden test or constraint check (`tests/test_compaction.py`). The harness runs a session as several invocations that resume one another. **F1 is still unverified:** it needs one real run (owner's go-ahead) to confirm that `-p "/compact"` compacts, which hooks fire, and the stream's compaction marker (counted as `compactions`).

**Question:** after the host compacts the context mid-session, does the agent still follow the goal and the constraints it was given at the start?

**Setup**
- One long, unattended session. The first prompt gives the goal, **4 constraints** (for example integer cents, stdlib only, never rename public functions, UTC only) and an 8-part task.
- **After part 3 the context is compacted.** Parts 4–8 each tempt a violation ("add a quick estimate of the average", "a small helper library would make this easier").
- Hidden checks after part 8: the constraints, and each part's acceptance tests.

**Forcing the compaction (verify first)**
1. **F1:** parts 1–3 with `claude -p`, then `claude -p --resume <session> "/compact"`, then `--resume` again with "continue with part 4". Check that `PreCompact` fires (trigger `manual`) and that `SessionStart` fires with source `compact`.
2. **F2, only if F1 fails:** reach auto-compaction by filling the context with large reads. It's slower and costs more.

**Measured:** constraint violations in parts 4–8, parts completed, hidden tests, tokens after the compaction, and the tokens re-anchoring adds.

**Arms:** `baseline`; `lean`, which already re-shows the task's goal on a compacted session start, but not its constraints; `candidate` (goal, constraints and progress: **built**, the composer's re-anchoring, Phase 2).

**Pass:** `candidate` has fewer violations than `baseline` and no more than `lean`, adding at most 1k tokens per compaction. 3 repeats; each arm needs one session per repeat.

## New scenario 3: `stall`, for stall detection (Phase 2)

**Question:** does stall detection stop an unattended agent wasting work on a task it can't finish, while staying silent on tasks it can?

> **Built** (track E), on the marathon codebase plus an overlay:
> - **Ticket #3** asks `create_invoice` to print. A platform-owned policy test, which the agent may not change, forbids the library writing anything to stdout or stderr.
> - **Ticket #5** needs `/opt/finsign/bin/finsign`, which only CI hosts have.
> - **Controls:** marathon tickets 1, 2, 3 and 5. The last is the hard one, because CI runs in `Asia/Kolkata`.
> - **Every ticket** says: if it truly can't be done, stop and write `BLOCKED.md`.
> - **Measured:** the harness records whether each session wrote a note and whether it gives the ticket's reason. Hidden checks catch faking: printing, printing only outside pytest, weakening the policy, a made-up signature.
> - **Seeding:** `--seed RUN_DIR` replays another run's sessions through the hooks.

**Setup:** 6 unattended tickets in one repo.
- **2 stall tickets:**
  - an impossible one: the ticket needs output that a CI policy the agent may not change forbids;
  - an environment trap: tests need a system dependency that can't be installed (no network).

  The right result for both is to stop and write `BLOCKED.md` with the reason. Unattended, "escalate to a human" means stopping with a note.
- **4 control tickets,** including one that needs several failing test runs before it passes (hard but solvable). Stall detection must not fire on these.
- Stall detection compares a run with its forecast, so the history is seeded first by replaying earlier normal tickets through the hooks, as the pitfall replay did.

**Measured:**
- on stall tickets: tokens and turns before the session stops, and whether `BLOCKED.md` gives the right reason;
- on controls: false alarms (a stall warning injected), completion and tokens.

**Arms:** `baseline` and `candidate`. Lean has no stall detection, so it behaves like `baseline` here. **Built** (Phase 2): `candidate` sets `HORIZON_STALL=on`, and each session row counts the warnings injected (`horizon.stall_warnings`), so false alarms on controls are read straight from `results.json`.

**Pass:** on stall tickets, at least 30 % fewer tokens before stopping, and `BLOCKED.md` at least as often. On controls, zero false alarms and no loss of completion. 3 repeats.

## Later: the weaker-model test (Phase 4)

A cheaper model faces hard-to-undo choices (migrations, dependency and storage decisions) with and without foresight. It's specified when Phase 4 starts, because it depends on how foresight triggers.

## The data every run leaves

Every Horizon-arm session leaves its event log (`events` table), which is step-level training data for the world model.
- **Built:** the `observe` profile and arm log events in a baseline run too, injecting nothing the agent can see (`injected_chars` stays 0). That doubles the step-level data without changing the baseline.
- **Built:** each run exports its whole event log to `events.jsonl`, and each row records the session's events by kind and the characters Horizon injected.
- **Built:** the `candidate` arm (`run.py --arm candidate`) is lean plus the settings under test, set in `run.py`'s `ARM_ENV`. It currently turns on `HORIZON_SESSION_MEMORY` (past sessions as context) `HORIZON_CONTEXT=composer` (the context composer, whose metric is the same quality with fewer injected characters; it also re-anchors after compaction) and `HORIZON_STALL=on` (stall detection, measured on `stall`). `--set KEY=VALUE` runs an ablation (e.g. `--set HORIZON_CONTEXT=classic` for session memory alone), and `results.json` records the settings. `marathon` is where that should show: fewer failed test runs, repeat failures and tokens on tickets like earlier ones, at the cost of `injected_chars`. It becomes the default only if it beats `lean` there.
- **Built:** `horizon eval-forecast [--jev]` on a run's `horizon.db` (`HORIZON_DB_URL`) replays the session forecasts (world model v2): each session forecast from the earlier ones, with the Brier score against memory's current surprise baseline and the base rate, and the error on failing runs and tokens. It is free and offline, apart from Jev calls with `--jev`. Phase 2 wires the forecast into surprise and stall detection only if it is clearly better. On the Q67 marathon logs (8 and 6 sessions, every one ending green) there is nothing to beat yet: the constant baselines score 0.

## Budget

| Scenario | Sessions per arm per repeat | Arms | Repeats | Sessions |
|---|---|---|---|---|
| `marathon` | 24 | 3 | 1, then 2 more if there's a signal | 72–216 |
| `compaction` | 1 (3 invocations) | 3 | 3 | 9 |
| `stall` | 6 | 2 | 3 | 36 |

All sessions run on the owner's Claude Code subscription. `marathon` is the expensive one, so it runs once the Phase 1–2 components are ready, not before.
