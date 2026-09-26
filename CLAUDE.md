# CLAUDE.md

Instructions for every Claude session in this repository.

## Before any work

- Always read `docs/PROJECT.md` and `docs/MEMROUTER.md` in full before doing anything.
- Read `PROGRESS.md` to see what is done, what is in progress, and what questions are still open.

## Source of truth

- `docs/PROJECT.md` is the source of truth. Follow its build order (§13).
- `docs/MEMROUTER.md` is the detailed memory spec. Where it conflicts with `docs/PROJECT.md`, `docs/PROJECT.md` wins.
- If something the task needs is unclear or contradictory in the docs, don't guess. Record it under "Open questions" in `PROGRESS.md` and ask.

## Scope

- Build only the phase or task requested in the session. Do not start later phases or add unrequested features.

## How to work

1. **Plan first.** Before writing code, lay out the plan and list the acceptance criteria for the requested phase or task.
2. **Write tests** for the behaviour being built.
3. **Confirm every acceptance criterion** is met, with the tests passing, before finishing. If a criterion can't be met, say so plainly.

## End of every session

1. **Update `PROGRESS.md`:** Completed, In progress, Decisions made, Open questions, Next step.
2. **End with a plain-language summary** covering:
   - What changed
   - Test results
   - Open questions
   - Next step

All four proposed defaults are approved: Docker when available with per-task local setup as fallback, your difficulty weights, the first 10 of the 50 tasks as the smoke run, and per-token prices in config if litellm lacks claude-sonnet-5.

Standing rule from now on. Add this to CLAUDE.md so it applies to every session:

DECISION RULE
- When a choice is not covered by the docs, pick the most reasonable default yourself, proceed, and log it in PROGRESS.md under "Decisions made" with a one-line reason.
- Do not stop to ask about implementation details, naming, library choices, parameters, file structure or anything easily changed later.
- Only stop and ask me when:
  1. It needs credentials, accounts, or spending money.
  2. It is hard to reverse (data deletion, public releases, schema changes affecting stored data).
  3. It contradicts PROJECT.md or MEMROUTER.md.
  4. You are truly blocked and cannot continue with any reasonable assumption.
- Collect any non-blocking questions and list them once at the end of the session under "Open questions", with the default you used for each.

Continue with the Phase 1 harness now. Do not wait for further confirmation.


