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

## Cursor agent: marathon, `baseline`, `lean` and `candidate` (2026-10-01, Cursor CLI with composer-2.5, 1 run each)

The same scenario driven by Cursor's agent (`--agent cursor`), with Horizon installed as Cursor hooks (`install-cursor`). All three arms ran in parallel. `baseline` finished all 24 tickets. `lean` lost its connection to Cursor's backend in ticket 22, and `candidate` hung on a reconnect in ticket 16. Both were cut by the infrastructure, not by the agent. The comparison covers the tickets every arm finished.

| Tickets 1–15 | Hidden tests failing at the end | Tickets ending with a hidden failure | Violations | Test runs failing | Tool calls | Tokens | Wall time |
|---|---|---|---|---|---|---|---|
| baseline | 0 | 0 | 0 | 0 | 428 | 3.42 M | 20 min |
| lean | 1 | 12 | 0 | 3 | 386 (−10 %) | 3.13 M (−8 %) | 22 min |
| candidate | 2 | 12 | 0 | 3 | 404 (−6 %) | 3.30 M (−4 %) | 24 min |

On tickets 1–21 (baseline against lean): baseline ended with 0 hidden failures, 625 tool calls and 5.18 M tokens; lean ended with 1 failure, 586 calls and 5.09 M tokens.

**Ticket 4 (persistence) decided it, and it looks like single-run noise.** Each Horizon arm got ticket 4 wrong in a different way, and nothing afterwards sent it back:
- lean broke `open_store(None)` and reset;
- candidate broke the survives-a-restart test, then fixed it in ticket 16;
- candidate also broke the monthly statement in ticket 8.

Baseline got every ticket right. Neither mistake touches anything Horizon said. Ticket 4's context was the session-start note (266–1,217 characters) plus, on Cursor, a stray line pointing at an MCP tool Cursor doesn't have. That line is now fixed. With one run per arm this isn't a Horizon effect either way, but it is not a gain. All three arms had 0 violations and 0 refused calls: headless Cursor applies no permission list, so refused-command memory has nothing to act on there.

**What this run shows:**
- Horizon works on a second agent with no MCP server and no instructions file. The captures were correct: one prompt per session, test runs read from both success and failure hooks, and context grew as tickets accumulated.
- The efficiency gap is small (lean −8 % tokens and −10 % tool calls), within the noise seen on Claude Code.
- Hidden tests are the real signal, and here baseline won.

### Ticket 4 repeated (Cursor, composer-2.5, 3 runs per arm, question 82)

Every run started from baseline's repo after ticket 3. Horizon arms were seeded with that run's test history and received 184 characters at session start.

| Ticket 4 | Runs that passed every hidden test | Hidden test missed | Tokens per run (mean) | Tool calls (mean) |
|---|---|---|---|---|
| baseline | 2 of 3 | `open_store(None)` and reset, once | 298 k | 42 |
| lean | 2 of 3 | `open_store(None)` and reset, once | 386 k | 36 |
| candidate | 1 of 3 | `open_store(None)` and reset, twice | 233 k | 26 |

**Ticket 4 is a trap for this model in every arm, Horizon or not.** Baseline also misses the `open_store(None)` behaviour. Counting the marathon run, each arm passed ticket 4 as follows: baseline 3 of 4, lean 2 of 4, candidate 1 of 4. Differences that size in four runs are noise; Fisher's exact test gives p ≈ 0.5 for candidate against baseline. The marathon's 1–15 gap came from this ticket, and nothing Horizon said caused it. Horizon doesn't prevent it either: the miss is a hidden behaviour the visible tests never exercise, so the test history Horizon reads has nothing to warn about.

## Refused-command memory: `candidate` with `HORIZON_DEAD_ENDS=on` against Phase 3 (2026-10-01, Sonnet, 1 run each)

These are the same setups as the Phase 3 runs. Stall was seeded from the question 67 lean run. Marathon stopped at the usage limit in ticket 7, so it covers tickets 1–6. Refused calls are counted from each run's stream; Phase 3 used the same reader.

| | Refused calls | Tokens | Warnings | Hidden tests, violations |
|---|---|---|---|---|
| stall, Phase 3 candidate | 13 (ticket 5: 8) | 4.62 M (ticket 5: 1.27 M) | — | all pass, 0 |
| stall, with refused-command memory | **8** (ticket 5: 7) | 4.58 M (ticket 5: **1.53 M**) | 2, both on ticket 5 | all pass, 0 |
| marathon 1–6, Phase 3 lean | 1 | 5.79 M | — | all pass, 0 |
| marathon 1–6, Phase 3 candidate | 8 | 5.22 M | — | all pass, 0 |
| marathon 1–6, with refused-command memory | **2** | 5.50 M | 0 | all pass, 0 |

**Fewer refused calls, no token saving.**
- **Stall ticket 5:** the warning fired at the third `finsign` attempt, and the agent stopped calling it (5 attempts before, 3 now). It then spent its turns elsewhere and used more tokens, not fewer.
- **Stall ticket 4:** refused calls fell from 4 to 0 and tokens fell from 0.90 M to 0.66 M, with no warning shown. The refusals in seeded history were listed at session start (1,616 characters against 1,179).
- **Marathon:** refused calls fell from 8 to 2, but tokens were in between lean's and Phase 3 candidate's.

Refusals are a small share of a session's cost, and per-ticket tokens swing ±50 % between runs, so one run can't show a token effect. The behaviour works as designed: it warns once, the agent stops, and nothing is lost. Not a gate pass: principle 5 asks for better outcomes or lower cost, and neither is shown.

## Cursor agent: `baseline` against `lean`, marathon tickets 1–12, 3 repeats each (2026-10-01, composer-2.5)

Six runs ran in parallel, and none needed a retry. Values are mean ± standard deviation over the 3 runs.

| Tickets 1–12 | Hidden tests failing at the end | Violations | Test runs failing | Tool calls | Tokens | Wall time |
|---|---|---|---|---|---|---|
| baseline | 1.33 ± 0.58 | 1.0 ± 1.7 (one run: a `float()` in `fx.py`, 3 tickets) | 1.7 ± 1.2 | 330 ± 17 | 2.90 M ± 0.17 | 16.7 ± 0.9 min |
| lean | 1.33 ± 1.15 (one run clean) | **0 in all 3** | 3.3 ± 1.2 | 340 ± 15 | 2.88 M ± 0.32 | **20.3 ± 0.8 min (+21 %)** |

**Lean is no better than baseline here, and it is slower.**
- **Hidden tests and tokens:** the same within noise. The misses are the same traps in both arms: `open_store(None)` (ticket 4), survives-a-restart (ticket 4) and the UTC monthly statement (ticket 8). Visible tests don't cover them, so lean has nothing to carry forward.
- **Violations:** lean had none in 3 runs. One baseline run broke the integer-cents rule from ticket 10 on. That fits what lean exists for (it re-states session 1's constraints at each start), but 0 of 3 against 1 of 3 isn't evidence.
- **Wall time:** lean took 3.6 minutes more per 12 tickets, about 0.6 s per tool call. Cursor waits for each hook, and every Horizon hook starts a new Python process. That cost is certain, and it's ours to remove.
- **The earlier single run's −8 % tokens for lean** (tickets 1–15) doesn't hold up: over three runs tokens are equal.

## Hook latency fixed (2026-10-01, Cursor, composer-2.5, lean, marathon tickets 1–12, 3 runs)

What changed:
- **Cheaper hooks:** numpy is imported only when a vector is stored, and model validation is built on first use.
- **Fewer hooks:** Cursor's post-tool hooks fire only on the tools Horizon reads (shell and edits), as on Claude Code, instead of on every read and search. `preToolUse` is no longer installed on Cursor.
- **Result:** one hook takes 0.16 s instead of 0.21 s, and hooks fire on about 40 % of tool calls instead of all of them, plus every shell call.

| Tickets 1–12 | Wall time | Tokens | Tool calls | Hidden tests failing at the end |
|---|---|---|---|---|
| baseline (earlier) | 16.7 ± 0.9 min | 2.90 M | 330 | 1.33 |
| lean before | 20.3 ± 0.8 min | 2.88 M | 340 | 1.33 |
| lean after | **16.1 ± 0.7 min** | 2.82 M | 341 | 2.33 ± 0.58 |

**The 21 % slowdown is gone.** These runs also had the lean session-start fixes from ticket 2 on: no wording that asks for tool calls, and goals without the shared preamble. Hidden failures were 2.33 against 1.33, from the same ticket 4 trap plus UTC-date misses in one run. With 3 runs that's within noise, but worth watching.

## Handoff: rules given early, needed late (2026-10-01, Cursor, composer-2.5, 8 tickets, 3 runs per arm)

A new scenario:
- **Ticket 1** gives two rules for later features: a fines cap ("must never exceed the replacement cost") and DD/MM/YYYY dates in CSV exports.
- **Ticket 2** gives one: email masking in anything that leaves the system.
- **Tickets 6–8** need those rules, and their prompts don't repeat them. Tickets 3–5 are controls.

Keyword capture recognises only the fines rule; Jev recognises all three. The Jev arms didn't actually use Jev, though: Cursor replaces secret-looking environment variables with `...` before running hooks, sandboxed or not. Jev got a 401 and the splitter fell back to keywords, so all four arms below captured only the fines rule.

| Arm (3 runs) | Fines cap | Report masks emails | Export dates DD/MM/YYYY | Export masks emails | Tokens per run | Wall time |
|---|---|---|---|---|---|---|
| baseline | 1/3 | 0/3 | 1/3 | 0/3 | 2.21 M | 17.3 min |
| lean | 1/3 | 1/3 | 1/3 | 1/3 | 1.47 M | 17.1 min |
| lean (Jev blocked: keywords) | 2/3 | 0/3 | 2/3 | 0/3 | 1.40 M | 17.2 min |
| candidate (Jev blocked: keywords) | **3/3** | 0/3 | **3/3** | 0/3 | 1.62 M | 17.5 min |

All functional tests passed, and there were 0 violations in every run.

**What decides it is whether the agent writes the rules down in ticket 1.** In all 12 runs, the fines cap and the DD/MM/YYYY dates passed exactly when the agent had recorded the board's rules in `README.md` during ticket 1, and failed otherwise. Every candidate run happened to write the README (6 of the other 9 runs did not), so candidate's 3/3 isn't evidence that its memory worked.

**Plain lean dropped a rule it had captured.** Lean recorded the fines rule in every run but showed constraints only from the last 3 tasks. By ticket 6 the rule was gone, and the three lean runs that hadn't written it down billed uncapped fines. Fixed: lean now lists every constraint the project's tasks set, as the composer does.

**Email masking is the gap memory should close.** Every run wrote a masking helper in ticket 2. In tickets 7 and 8, all but one run didn't use it in the report or the export. No arm captured that rule, since only Jev recognises it.

**Tokens:** lean used 1.40–1.62 M per run against baseline's 2.21 M (−27 % to −37 %). With 3 runs and a 4 % wall-time gap, that's a lead, not a result.
