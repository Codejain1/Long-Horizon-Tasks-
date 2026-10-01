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

**Replay 3:** no Horizon broke the policy in 2 of 5 sessions, and lean in **0 of 5** (warning shown every time). Cost $0.76 against $0.77. **Across 3 replays: 7 of 15 sessions against 0 of 15, at 12 % lower cost overall ($2.48 against $2.19).**

## Live backlog 2: the whole backlog from scratch, no seeding (2026-09-30)

| | Sessions breaking the changelog policy | Warning shown | Cost |
|---|---|---|---|
| no Horizon | 4 of 8 (tickets 3, 6, 7, 8) | – | $1.14 |
| lean | 3 of 8 (tickets 1, 3, 4) | **never** | $1.32 |

**The warning never fired, which exposed the most important bug this eval found.** When a Bash command exits non-zero, Claude Code fires `PostToolUseFailure`, not `PostToolUse`, with the output in `error`. Horizon listened only to `PostToolUse`, so it **saw every passing test run and no failing one**. That was true in every profile, including the full workflow's own regression judging whenever the agent didn't report counts itself. A live probe confirmed the payload. Horizon now installs its capture hook on both events and reads `error`. The live run is being repeated with the fix.

## Live backlog 3: lean from scratch with failing runs captured (2026-09-30)

| Ticket | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Changelog policy broken | – | – | – | ✗ | ✗ | – | – | – |
| Pitfall warning shown | – | – | – | – | – | ✓ | ✓ | ✓ |

**The whole loop works live.** Horizon recorded the two failing CI runs (tickets 4 and 5) from real `./ci.sh` output. From ticket 6 on, session start warned about the check, and no session broke it again. Across the whole backlog, lean broke the policy in **2 of 8** sessions, against 5 of 8 and 4 of 8 in the two live runs without Horizon. The early sessions pay the discovery price; the rest don't. Hidden tests were 13/13. Cost was $1.25, against $1.20 and $1.14 without Horizon: about +6 % over a backlog this short, where only 3 sessions came after the warning. The replays, which start with the history, came out 12 % cheaper.

## Real codebase: 8 consecutive attrs changes as an unattended backlog (`evals/realrepo/`, 2026-10-01, Sonnet)

Each real change was posed as the issue it solved. Grading used the change's real tests, the whole suite, and attrs' changelog-fragment rule. The usage limit cut both arms at task 8 (no Horizon never ran it; lean's run was aborted early), so tasks 1–7 are compared.

| Tasks 1–7 | Resolved | Changelog fragment | Failed / all CI runs | Turns | Cost |
|---|---|---|---|---|---|
| no Horizon | 6/7 (task 6 missed) | 7/7 | 3/24 | 233 | $3.40 |
| lean | 6/7 (task 6 missed) | 7/7 | 2/20 | 199 | $3.20 (−6 %) |

- **No difference in outcome, and no extra cost.** Both arms solved the same six real issues and missed the same hard one (the converter decorator).
- **Nothing for Horizon to learn here.** attrs' convention is visible in the repo: `changelog.d/` is full of fragments. Both agents added a fragment every time without being told, and the changelog check never failed. The CI failures were ordinary bugs, each in a different test, so no pitfall recurred and the warning never fired.
- **It fits the earlier finding:** when the knowledge is in the repo, the agent finds it itself. Horizon's gains came where it wasn't: rules stated once in a prompt, policy failures repeating across sessions, and teammates. And lean costs nothing when it has nothing to add.
