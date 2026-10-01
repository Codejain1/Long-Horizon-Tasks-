# Results

## Run 1: 2026-09-29, Sonnet 5, Horizon with live Jev, one run per arm

| | Hidden tests at the end | Constraint violations | Turns | Tool calls | Horizon calls | Cost-equivalent | Tokens |
|---|---|---|---|---|---|---|---|
| ledger / baseline | 4/4 | 0 | 77 | 73 | 0 | $1.15 | 2.56 M |
| ledger / Horizon | 4/4 | 0 | 77 | 73 | 17 | $1.53 (**+33 %**) | 3.15 M (+23 %) |
| notes / baseline | 4/4 | 0 | 59 | 55 | 0 | $0.96 | 1.76 M |
| notes / Horizon | 4/4 | 0 | 111 (**+88 %**) | 107 | 24 | $2.30 (**+140 %**) | 5.14 M (+192 %) |

**Horizon made no difference to quality, and it cost more.** Both arms passed every hidden test after every session and kept every constraint.

Why:
1. **The baseline kept its own memory.** It wrote `NOTES.md` and `DESIGN.md` with the constraints and design, and read them in later sessions. That is the native project-memory mechanism `PROJECT.md` §2 already acknowledges.
2. **The early decision didn't bite.** Horizon's `evaluate_options` chose SQLite in session 1 (good). The baseline built a file-locking store that also survived 8 concurrent writers.
3. **Horizon's cross-session continuity never engaged (a product gap).** In all 8 Horizon sessions, the agent marked the task complete at the end of the session. The next session started a new task, so the session-1 goal and constraints were never shown to sessions 2–4. Horizon's task state is per task, but "come back tomorrow and continue" is how multi-session work actually happens.
4. **The overhead was real:** 17–24 extra Horizon calls, plus the deliberation they trigger (the notes Horizon arm spent 34 turns on session 3, against the baseline's 14).

**Caveat:** one run per arm, on two small scenarios. A strong model on small projects is exactly where a plain notes file is enough. The scenarios may be too easy to show a difference either way.

## Ledger re-run: 2026-09-29, with finished tasks' constraints shown at session start (Horizon arm only)

4/4 hidden tests in every session, 0 violations, 80 turns, $1.54. Sessions 2–4 were shown session 1's goal and copied its four constraints into their own tasks. **The fix works, but on this scenario it changes nothing:** the baseline never drifted, so the cost overhead remains.

## Team: 3 projects in fresh repos, the team's rules stated only in project 1

Run 1 (Horizon before team rules) was cut short in project 3 by the usage limit. Up to that point, project 2 without Horizon lost the id and timestamp rules (2/4). With Horizon it kept the timestamp rule, only because a stored episode from project 1 happened to match the situation, and lost the id rule (3/4). That led to **`start_task(team_rules=…)`**, shown at session start in every project.

Run 2 (with team rules). Project 3's hidden test was then loosened: its prompt never asked for serialised stamps, so aware UTC datetimes are accepted. Both arms were re-scored.

| | Project 1 (rules stated) | Project 2 | Project 3 | Projects 2–3 | Turns | Cost-equivalent |
|---|---|---|---|---|---|---|
| baseline | 4/4 | 2/4 (dashed ids, `+00:00` stamps) | 3/4 (dashed ids) | **5/8** | 35 | $0.66 |
| Horizon | 3/4 (a negative amount not rejected) | 4/4 | 4/4 | **8/8** | 48 | $0.91 (+39 %) |

**The first scenario where Horizon helps.** When the rules must travel to a new project, where the baseline's notes file can't follow, Horizon kept every rule and the baseline lost them. The help came from a plain mechanism: stored rules shown at session start. It did not come from the decision layer or the learned memory. Horizon's project-1 miss is an ordinary lapse on the one session where both arms had the rules in the prompt. One run each, so this is a signal, not proof.

## Ablation: no Horizon, lean and full, team scenario, 3 repeats each (2026-09-29, Sonnet, live Jev)

Hidden tests passed per project (1, 2, 3); cost is the sum of the 3 sessions.

| | Rep 1 | Rep 2 | Rep 3 | Projects 2–3, all reps | Turns (mean) | Cost (mean) |
|---|---|---|---|---|---|---|
| no Horizon | 4, 2, 3 | 4, 2, 3 | 4, 2, 3 | **15/24** | 28.7 | $0.56 |
| lean | 4, 4, 4 | 4, 4, 4 | 4, 4, 4 | **24/24** | 37.7 | $0.87 (+55 %) |
| full | 4, 4, 4 | 4, 4, 4 | 3, 4, 4 | **24/24** | 46.3 | $1.00 (+78 %) |

- **The effect is consistent.** Without Horizon, the team's rules were lost in every repeat: dashed ids and `+00:00` stamps in project 2, dashed ids in project 3. Lean and full kept them every time. Full's one miss was in project 1, where the rules were in the prompt.
- **Lean keeps all the gain for 30 % less overhead than full.** Its remaining overhead is 3 turns per session: Claude Code's tool search to load Horizon's deferred tools, `start_task`, and `record_outcome`. That's +9 turns per run, all of lean's difference. The context Horizon adds is small: about 1.4k tokens at session start, and about 0.5k for the `start_task` result.
- **Caveat on the competitor:** a user who writes their team rules into `~/.claude/CLAUDE.md` gets the same rules in every project at no Horizon cost. Horizon's edge is capturing the rules automatically as the user states them, and, hosted, sharing them across people and machines. That needs a test with more than one person or machine.

## Hook-based lean against no Horizon, team scenario, 3 repeats (2026-09-30)

Lean now captures the task and rules from the `UserPromptSubmit` hook. Reps 1–2 ran with the server still offering `start_task`/`record_outcome`, and the agents called each once per session. From rep 3 on, the lean server leaves them out.

| | Rep 1 | Rep 2 | Rep 3 | Projects 2–3 | Turns (mean) | Cost (mean) |
|---|---|---|---|---|---|---|
| no Horizon | 4, 2, 3 | 4, 2, 3 | 4, 2, 3 | **15/24** | 28.7 | $0.55 |
| lean (hooks) | 4, 4, 4 | 3, 4, 4 | 3, 4, 4 | **24/24** | 37.7 | $0.77 (+40 %) |

- **Quality holds:** 24/24 on the projects where the rules had to be remembered. Lean's misses were two project-1 slips (a negative amount not rejected), where the rules were in the prompt for both arms.
- **The remaining cost is mostly doing the work right.** In rep 3 lean made **zero** Horizon calls, one tool search aside, and still took 38 turns against 28. It wrote more code, with validation, UTC serialisation and tests for them: 164 and 250 lines in projects 2–3, against 142 and 138. What Horizon adds itself is about 1.5k tokens of session-start context. The rest is the price of following the team's rules, which the baseline skipped.

## Backlog: 8 unattended tickets on an existing codebase with CI policy checks (2026-09-30, Sonnet, 1 run each)

| | Hidden tests (tickets + 5 policies) | Sessions with a failed test run | Failed / all test runs | Turns | Cost |
|---|---|---|---|---|---|
| no Horizon | 13/13 after every session | **6 of 8** (changelog check 5×, rounding 1×) | 6/16 | 115 | $1.20 |
| lean | 13/13 after every session | **2 of 8** (changelog check, sessions 1 and 8) | 2/12 | 124 | $1.36 (+13 %) |

- **The recurring pitfall is real.** Without Horizon, fresh sessions broke the same CI policy ("every public function is in the changelog") in 5 of 8 tickets, found it by failing CI, and fixed it. That's the unattended-agent failure memory should prevent.
- **Lean broke it far less, but not because of the pitfall warning.** The warning needs failures in 2 earlier sessions, and lean failed only once before ticket 8, so it never fired. Lean's session start did list the earlier tickets. Whether that prompted more care, or this is chance, one run can't tell.
- **Both arms ended with every ticket and policy passing,** so quality is equal. Lean cost 13 % more. The warning mechanism itself is untested: it needs a run where the pitfall recurs under Horizon, or a replay that seeds a history of failures.
- The failed-run counter was fixed before these numbers: JSON-escaping newlines had hidden "1 failed". The counts were recomputed from the saved transcripts.

## Picking out rules: keywords against Jev (`evals/rules/`, 2026-09-30)

Each sentence of a prompt is classified as a team rule (all projects), a project constraint, or neither. On 32 labelled sentences:

| | Accuracy | Misses |
|---|---|---|
| keywords | **62 %** | Rules without trigger words ("For any code you write for me, use type hints", "This package must keep supporting Python 3.9"). "Never mind…" and "Always a pleasure…" taken as rules. |
| Jev (two yes/no questions per sentence, one request, about 1 s) | **97 %** | "Let's never do a release on Friday again" filed as a team rule (arguably right). |

Jev is used whenever `HORIZON_SCORER=jev`, with keywords as the fallback. The labelled set is small and written by us, so this is a first measurement.

**Found while building the replay:** Horizon's test-run capture recognised only known commands like `pytest` and `npm test`, so CI run through `./ci.sh` was **never captured**. In the backlog and team runs, lean's pitfall memory therefore couldn't see most test runs. Capture now also accepts wrapper scripts and task-runner test targets, when their output parses as a test run.

## Pitfall warning, tested directly: backlog tickets 4–8 replayed (2026-09-30, Sonnet, 1 run each)

Both arms start from the no-Horizon run's repo after ticket 3. Lean's memory is seeded by replaying that run's real test runs (tickets 1–3) through Horizon's own `PostToolUse` hook. The changelog policy had failed in 2 of those sessions, so lean's session start says: *"Checks that failed in several earlier sessions here (get them right the first time): tests/test_policy.py::test_every_public_function_is_in_the_changelog (failed in 2 sessions)"*.

| | Warning shown | Sessions breaking the changelog policy | Failed / all test runs | Hidden tests | Turns | Cost |
|---|---|---|---|---|---|---|
| no Horizon | – | **3 of 5** (tickets 4, 5, 6) | 3/11 | 13/13 | 80 | $0.90 |
| lean | 5 of 5 sessions | **0 of 5** | **0/6** | 13/13 | 71 | **$0.75 (−17 %)** |

**The first result where Horizon is both better and cheaper.** Warned up front, each session updated the changelog as it went, so no CI run failed. Without the warning, fresh sessions kept rediscovering the policy by breaking it: 3 of 5 here, matching the original run's 3 of 5 for tickets 4–8. They paid for the extra test runs and fixes. One run each, from seeded history, so this is a strong signal, not proof.

**Replay 2** (same setup): no Horizon broke the changelog policy in 2 of 5 sessions (tickets 6 and 7), and lean in **0 of 5**, with the warning shown every time. Cost: $0.82 against **$0.67 (−18 %)**, turns 79 against 63. Hidden tests 13/13 for both. A third replay was cut short by the usage limit after ticket 4; it's being re-run.

## Past sessions as context: `candidate` against `lean`, marathon tickets 1–6 (2026-09-30, Sonnet, 1 run each, v2 Q65/Q67)

`candidate` is lean plus `HORIZON_SESSION_MEMORY=on`: a new ticket's first message listed similar past sessions (how each ended, the files it changed, the tests that failed on the way). Both arms ran tickets 1–8. `candidate` hit the subscription's usage limit during ticket 7, so the comparison covers tickets 1–6.

| Tickets 1–6 | Hidden tests | Violations | Turns | Test runs (failing) | Tokens | Cost-equivalent | Injected chars |
|---|---|---|---|---|---|---|---|
| lean | 17/17 | 0 | 160 | 14 (1) | 5.05 M | $2.23 | 5,324 |
| candidate | 17/17 | 0 | 206 (**+29 %**) | 17 (2) | 7.29 M (**+44 %**) | $2.97 (**+33 %**) | 8,788 |

**The note cost more and saved nothing.** Every earlier ticket had passed cleanly (lean had one failing test run in six tickets). So the note held nothing to avoid, only file lists (`CHANGELOG.md`, `README.md`, `backup.py`, …), which plausibly invited extra reading. Ticket 4 took 60 turns against lean's 42.

**Caveat:** one run each. Ticket 1 had no note in either arm, yet already differed by 5 turns, so the noise is large.

**Changed as a result:** the note now shows only past sessions that hit trouble (a failing test run on the way, or failures left at the end), so a clean history adds nothing. `HORIZON_SESSION_MEMORY` stays off (question 67).

## Phase 3 gate: compaction, `baseline`, `lean` and `candidate` (2026-10-01, Sonnet, 3 repeats each)

`candidate` is lean plus `HORIZON_SESSION_MEMORY=on`, `HORIZON_CONTEXT=composer` and `HORIZON_STALL=on`. The compaction scenario states three constraints in the first message, runs `/compact` partway, then asks for parts that hit each constraint. `/compact` on a resumed headless session does compact (question 62): one `compact_boundary` per run, and Horizon sees a `compact` session start.

| Arm | Violations after compaction (repeats 1/2/3) | Part 6 | Mean tokens | Mean cost-equivalent | Injected chars |
|---|---|---|---|---|---|
| baseline | 2 / 0 / 0 (float money in repeat 1) | failed 3/3 | 792 k | $1.11 | 0 |
| lean | 0 / 0 / 0 | failed 3/3 | 893 k | $1.28 | 1,754 |
| candidate | 0 / 0 / 0 | failed 3/3 | 877 k | $1.14 | ≈ 4,460 (the re-anchor at the compact start is ≈ 2,230, ≈ 560 tokens) |

**Passes on paper, no gain over lean.** The candidate has fewer violations than baseline, none more than lean, and costs under 1 k tokens per compaction. But lean already reached zero; the restated task added nothing measurable. Part 6 (India's "today" against the all-times-UTC rule) failed in all 9 runs, even with the constraint restated right after compaction. The model treats a user-local date as outside the rule, so this is a judgement trap rather than a forgetting one.

## Phase 3 gate: stall, `baseline` against `candidate` (2026-10-01, Sonnet, 1 run each)

Six tickets: 2 that can't be finished (ticket 3 conflicts with a CI policy, ticket 5 needs a missing tool `finsign`) and 4 controls. Both arms were seeded with the 7 session episodes of the question 67 lean marathon run, so the forecast had history. The stall threshold came out at the floor (4 failing runs) for every ticket.

| | Stall tickets: right `BLOCKED.md` reason | Tokens on stall tickets (3 / 5) | Controls: hidden tests, violations | Tokens on controls | Stall warnings |
|---|---|---|---|---|---|
| baseline | 2/2 | 382 k / 322 k | all pass, 0 | 2.57 M | — |
| candidate | 2/2 | 243 k / **1.27 M** | all pass, 0 | 3.11 M | 0 |

**Stall detection had nothing to catch.** No session in either arm had even one failing test run on a stall ticket: the model read the policy test and stopped (ticket 3), or probed for `finsign` with ordinary commands (ticket 5). Candidate's ticket 5 took 31 turns re-running the missing tool and checking whether commands were blocked before writing a correct note. That is an exploration stall the detector doesn't watch, since it only counts failing test runs. Gate not met: candidate wasn't faster to stop, and the scenario can't exercise the detector as designed.

**Harness fix found here:** the `BLOCKED.md` check read the first 2,000 characters of the whole file, so a later ticket's correct reason was scored as wrong once earlier notes filled it. It now matches only the lines the session added (commit `7991b16`). The table above is re-scored with the fix.

## Phase 3 gate: marathon, `lean` against `candidate`, tickets 1–12 (2026-10-01, Sonnet, 1 run each, question 72)

Both arms ran in parallel and both hit the subscription's usage limit: `lean` during ticket 7, `candidate` during ticket 11. The comparison covers tickets 1–6.

| Tickets 1–6 | Hidden tests | Violations | Turns | Test runs (failing) | Tokens | Cost-equivalent | Stall warnings |
|---|---|---|---|---|---|---|---|
| lean | 17/17 | 0 | 172 | 15 (1) | 5.79 M | $2.65 | 0 |
| candidate | 17/17 | 0 | 169 | 18 (2) | 5.22 M (−10 %) | $2.42 (−9 %) | 0 |

**A wash, within noise.** Both arms were clean on every ticket. Candidate's per-ticket tokens swing from 0.55 M to 1.44 M, and its ticket 1 cost 1.8× lean's with identical context (266 characters injected in both), so a 10 % gap in one run says nothing. Candidate tickets 7–10 were also clean (all hidden tests pass, 0 violations). In ticket 11 it was cut off mid-work with the changelog policy test failing. Unlike the question 67 run, candidate cost no more than lean: the past-sessions note now appears only after trouble, and here there was almost none. No ticket produced a failing-run streak, so stall detection never had a chance to fire.
