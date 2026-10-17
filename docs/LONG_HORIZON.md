# Long-horizon system: design

The source of truth for the runner, its judge, its world model and how Hermes uses them. `README.md` says what
runs where. `PROGRESS.md` says what is done and measured.

## 1. Goal

A small, cheap model (Qwen3-14B today) does long, multi-step work reliably, because the system carries the plan,
the state and the checks. The model does one small step at a time.

**Headline test:** on the same long jobs, small model + system ≈ a frontier model alone, at a fraction of the cost.

**Product:** Hermes (WhatsApp) hands its long tasks to this system: instant projects, find-it-cheaper, flights and
hotels (prepare only, the owner pays), food orders (cash on delivery, the owner says yes).

## 2. Roles

| Role | Who | Sees | Produces |
|---|---|---|---|
| **Planner** | model | goal, constraints, memory (§6), project map (§3) | 3–10 steps, each with a done-check (§4) |
| **Executor** | model + tools (run, read, write, dispute_check, done) in a podman sandbox | goal card, its one step, outputs of the steps it uses, contract + fixed tests, memory for this step, the last error | the step's work and a short summary |
| **Watcher** | **code**, never a model | every check result, test counts, time | next action (§5) |
| **Judge** | model, calibrated (§7) | a question plus evidence | a probability that is right that often |
| **World model** | statistics over past jobs (§9) | the job's state so far | P(job ends solved), expected minutes |

Rules that hold for every role:
- The watcher runs every check itself; a model's "done" is never trusted alone.
- The goal card goes with every step, so a small step never loses the big picture.
- Thinking mode and bigger models are escalations, used when a step is stuck, not on every step.

## 3. Kinds of job

1. **Greenfield code:** build a small program from a goal. This works: when the runner says done, the hidden tests
   agree 63/64 times.
2. **Brownfield code:** extend an existing project (the backlog benchmark; most real work). **Broken today.** The
   2026-10-18 smoke run (rental ticket 1, Qwen3-14B) failed in every arm:
   - it replaced the package's `__init__.py` with stubs;
   - it created a stray `rentals.py` next to the package;
   - its tests assumed a data model the code doesn't have.

   It needs:
   - **Project map** at planning and in every step: modules, public functions with signatures, docstring first
     lines, how tests run (`IDEAS.md` #6; it made runs 7 % faster at equal quality on Claude Code).
   - **Existing tests are fixed from the start** (locked like the runner's own), and every build check also runs
     them. A step that breaks one has failed, whatever its own check says.
   - **No rewrite of existing modules:** writing a file that exists must keep its public names. The watcher
     compares exported names before and after, and refuses a write that removes one.
   - **A brownfield plan shape:** read → extend → test, with no SPEC.md from scratch. The contract is the existing
     code plus the ticket.
3. **Web and ops tasks without tests** (Hermes: find, compare, prepare). The done-checks are evidence checks and the
   judge (§4). The rails apply (§10).

## 4. Done-checks

Prefer the cheapest check that can't be fooled:

| Check | Passes when | For |
|---|---|---|
| `{"cmd": ...}` | the command exits 0 (with pipefail) | code, files, tests |
| `{"file": ...}` | the file exists | artefacts |
| fixed tests | every locked test passes (also ends the job early) | code |
| `{"evidence": path}` | every claim in the JSON file (`url`, `quote`) is found: the URL fetches and the quote appears in its text | web research: no invented facts or links |
| `{"judge": question}` | the calibrated judge says yes with p ≥ 0.7 | quality questions code can't answer ("does this answer the goal?") |
| `{"owner": question}` | the owner says yes on WhatsApp | money, messages to people, anything irreversible |

The planner must give a code or evidence check when one exists. A judge check never replaces a code check.

## 5. Watcher policy (after a failed check)

In order:
1. **Environment** failure (missing package or tool) → fix the environment, once.
2. **Progress** (more tests passing than the try before) → keep going (up to 5 tries).
3. **Escalate:** the same model in thinking mode, with the full spec. Later: a bigger model.
4. **Revise the spec:** only when the build is stuck. The judge reviews whether the fixed tests are possible; if
   they're impossible they are rewritten, never quietly edited.
5. **Re-plan** the remaining work (at most 2 re-plans). A check that just failed may not come back unproven.
6. **Stop and ask the owner** with what was tried.

Always:
- the time budget;
- the stall rule (the same check failing with no progress);
- locked tests are restored if edited;
- **early finish** when every fixed test passes;
- **(§9) stop early** when the world model says the job is doomed, once that model passes its gate.

## 6. Memory (memrouter)

The runner reads, and never writes:
- **the owner's standing rules:** confirmed, always shown;
- **warnings:** checks that failed in earlier jobs of this scope, from real outcomes;
- **what the owner said earlier** that may apply.

Whoever scores the delivery (hidden tests, QA, the owner's verdict) posts `/outcome` with the job's `warning_id`, so
memory learns which warnings help. Hermes's corrections ("I said under 200") are outcomes in scope `hermes`.

## 7. Judge (part 2)

`runner/judge.py`. A question plus evidence → `{"p", "raw", "reason", "id"}`.
- **Model:** the same OpenAI-compatible endpoint. It is asked for a probability and one line of reason, as JSON.
- **Calibration:** every judgment is logged in SQLite with its question family. When the real outcome is known,
  `label(id, outcome)` records it. With 20+ labels in a family, raw probabilities go through an isotonic map
  (pool-adjacent-violators) to the observed rate, so a 0.8 comes to mean 80 %.
- **Free labels:** at the end of every benchmark job the runner asks the judge "is this job done and correct?";
  the hidden tests then label it. Every coding run calibrates the judge for the tasks that have no tests.
- **Measured by:** Brier score and calibration error (ECE) on held-out labels, before and after calibration, and
  against the base rate.

## 8. Escalation and the big-model arm (part 4)

Any role can be given its own OpenAI-compatible endpoint and model: the planner, the spec writer, the judge. The
headline experiment runs the same backlog:

| Arm | What runs |
|---|---|
| small alone | plain loop, Qwen3-14B |
| small + system | runner + memory, Qwen3-14B |
| big alone | plain loop, a frontier model |
| small + system + big planner | the frontier model only plans and reviews |

Metric: cost to working software (fix-up rounds, minutes, tokens and dollars at list prices). A frontier model
needs an API key and spends money, so it is the owner's call.

## 9. World model (part 5)

`runner/forecast.py`. From past jobs (run logs + hidden outcomes), at any point in a job:
- **Features:** minutes so far, steps done / planned, checks failed, escalations, re-plans, the last test pass rate
  and its trend, the job family's base rate.
- **Model:** logistic regression in numpy (L2), trained on job states sampled every few events and labelled with
  the job's final hidden-test result.
- **Gate:** leave-one-run-out replay, compared with the family base rate. It must be clearly better on Brier
  (paired, 95 %) before it acts.
- **Use, after the gate:**
  - stop a job whose P(solved) < 0.1 after 40 % of its budget (saves GPU), or escalate it instead;
  - pick which jobs get the big model.

## 10. Rails (Hermes)

Enforced in code (`hermes/rails`), not in prompts:
- food is cash on delivery only, with the owner's yes;
- flights and hotels are prepared, never paid;
- no card numbers, CVVs, OTPs or UPI PINs;
- no secrets read;
- public links need the owner's yes.

Long tasks run in the podman sandbox. Anything with an `owner` check waits for the owner.

## 11. Evaluation

| Suite | What it decides |
|---|---|
| `bench/backlog.py` rental + ward | brownfield; cost to working software; arms plain / runner / notes / mem (+ big, §8) |
| `bench/bench.py` basic + house | greenfield; learning the owner's conventions |
| `bench/web.py` (part 3) | tasks without tests: evidence checks and the judge on real web sources |
| judge calibration | Brier and ECE, before and after |
| forecast replay | Brier vs the base rate, paired |

Acceptance for each part:
1. This document.
2. **Judge:** calibration lowers held-out Brier versus raw, and is no worse than the base rate.
3. **Web tasks:**
   - the evidence check rejects an invented quote and a dead link (tests);
   - live, the runner completes at least 2 of 3 real web tasks with every claim verified.
4. **Big-model arm:** any role can take its own endpoint (tests); the headline run waits on the owner's key.
5. **Forecast:** a replay over the existing runs. It's only switched on if it beats the base rate.
