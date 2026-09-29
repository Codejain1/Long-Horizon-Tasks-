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
