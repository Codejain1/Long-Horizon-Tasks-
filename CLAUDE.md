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
