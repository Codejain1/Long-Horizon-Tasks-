# CLAUDE.md

Instructions for every Claude session in this repository.

## Before any work

- Always read `docs/PROJECT.md` and `docs/MEMROUTER.md` in full before doing anything.
- Read `PROGRESS.md` to see what is done, what is in progress, and what questions are still open.

## Source of truth

- `docs/PROJECT.md` is the source of truth. Follow its build order (§13).
- `docs/MEMROUTER.md` is the detailed memory spec. Where it conflicts with `docs/PROJECT.md`, `docs/PROJECT.md` wins.
- If something the task needs isn't covered by the docs, follow the decision rule below. If the docs contradict each other or the task would contradict them, stop and ask.

## Decision rule

- When a choice is not covered by the docs, pick the most reasonable default yourself, proceed, and log it in `PROGRESS.md` under "Decisions made" with a one-line reason.
- Do not stop to ask about implementation details, naming, library choices, parameters, file structure or anything easily changed later.
- Only stop and ask the owner when:
  1. It needs credentials, accounts, or spending money.
  2. It is hard to reverse (data deletion, public releases, schema changes affecting stored data).
  3. It contradicts `docs/PROJECT.md` or `docs/MEMROUTER.md`.
  4. You are truly blocked and cannot continue with any reasonable assumption.
- Collect any non-blocking questions and list them once at the end of the session under "Open questions" in `PROGRESS.md`, with the default you used for each.

## Scope

- Build only the phase or task requested in the session. Do not start later phases or add unrequested features.

## How to work

1. **Plan first.** Before writing code, lay out the plan and list the acceptance criteria for the requested phase or task.
2. **Write tests** for the behaviour being built.
3. **Confirm every acceptance criterion** is met, with the tests passing, before finishing. If a criterion can't be met, say so plainly.

## Development

- Code lives in `src/horizon/`, tests in `tests/`. Set up with `uv venv -p 3.12 && uv pip install -e ".[dev,embeddings]"`.
- Run `.venv/bin/pytest`. Set `HORIZON_TEST_PG_URL` to a Postgres database with pgvector to run the Postgres variants too. Without it they are skipped.

## End of every session

1. **Update `PROGRESS.md`:** Completed, In progress, Decisions made, Open questions, Next step.
2. **End with a plain-language summary** covering:
   - What changed
   - Test results
   - Open questions
   - Next step
