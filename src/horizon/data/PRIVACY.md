# Data and privacy

Horizon is built to help coding agents **without ever needing your code**. Your agent (Claude Code, Codex) reads, writes and runs your code on your machine. Horizon receives short descriptions of decisions and their outcomes, never files, diffs or source (`PROJECT.md` §12).

This page lists exactly what is stored, where, and who else sees it. It's served at `/privacy` by the hosted server.

## What Horizon stores

| Data | Example | Notes |
|---|---|---|
| **Task goal** | "Add CSV export to the reports page" | Kept **verbatim** (the one exception to redaction), because staying on the user's exact goal is the point of task state (§8). Don't paste secrets or code into the request if you don't want them stored. |
| **Your messages (lean profile)** | the session's first message as the task goal; later messages as progress notes; sentences that set rules ("for every project…") as team rules or constraints | Captured by the `UserPromptSubmit` hook on your machine. Code blocks and long inline code are removed, the goal is capped at 4,000 characters, and notes at 300. Team rules are shown at session start in **every project of the team**. |
| **Decision summaries** | situation "choose a CSV writer", option "stdlib csv", reason, plan, constraints, progress notes, open issues | **Redacted before storage:** code blocks, code-like lines and stack traces are removed, and each field is capped (situation 500 characters, option 200, notes 300). |
| **Outcomes** | tests 9/10 passed; tokens, cost, latency if reported; surprise | Numbers only. |
| **Test results** | runner, counts, the **ids** of failing tests (e.g. `tests/test_api.py::test_login`) | Captured by the hook from your test runs: counts and test names only, **never the output**. The command is stored as its first line with quoted strings replaced (`python -c "…"`). |
| **Attempt usage** | 18,400 tokens (input, output, cache write, cache read), 12 model turns, 95 s | Summed by the hook on your machine from the Claude Code transcript, just before `record_outcome`. Counts and a duration only, **never transcript text**. |
| **Checkpoint references** | a git commit id; the time and a 120-character redacted snippet of your latest Claude Code prompt | References only. Git snapshots are made on your machine by `git stash create` and never leave it; Horizon stores the id. |
| **Consequence checks** | per option: which static checks passed, whether the spike passed, test counts, numeric metrics, one short note | Structured results only. The note is redacted. Spikes are built and deleted on your machine under `.horizon/spikes/`. |
| **Memories** | episodes (the records above), lessons and strategies distilled from them, links, strength | Derived from the stored records above, so no new kinds of content. |
| **Decision log** | the state the scorer saw (goal, constraints, situation, options, recalled past outcomes), scores, consequences, outcomes | For the world model (§6), which learns from these records and its own forecasts, logged with each decision. Exported with `horizon export-decisions`. |
| **Approvals** | "clear fear lesson X?" → accepted by "Kartik" | Question and answer (redacted), and the name you typed if the prompt asked for one. |
| **Accounts (hosted)** | team name, API key **hashes**, credit ledger, usage per call | API keys and web session tokens are stored only as SHA-256 hashes. |

**Not stored:** file contents, diffs, source code, test output, stack traces, environment variables, or your repository.

## Where it's stored

- **Local use** (stdio, the default): a SQLite file on your machine (`~/.horizon/horizon.db`, or `HORIZON_DB_URL`). Nothing leaves your machine unless you enable a scorer (below).
- **Hosted:** the operator's Postgres database. **Each team's data is isolated**: every query is scoped to the team resolved from the API key.
  - The hooks still run on your machine. They parse test output and snapshot git locally, and send the server only test counts, failing test names, token counts and durations, the command's first line with quoted strings elided, commit ids, and a **hash** of the project path (`X-Horizon-Project`). No paths, output or code are sent. In Claude Code, a checkpoint also carries a redacted 120-character snippet of your latest prompt, so `/rewind` can find it.
- **World-model exports** (Parquet and JSONL) are written where the operator points `HORIZON_EXPORT_DIR`. They hold the same records, never code.

## Who else sees it

- **Nobody, by default.** `HORIZON_SCORER=none` and the default local embedder (`BAAI/bge-small-en-v1.5`, running on your machine) send nothing out.
- **With `HORIZON_SCORER=jev`**, each decision's scoring request goes to **TypeSafe** (`api.typesafe.ai`). It contains the goal, constraints, situation, option labels and descriptions, and recalled past outcomes (all redacted, apart from the goal). The same applies to the memory attention filter (situation plus memory summaries).
- **With `HORIZON_SCORER=llm`**, the same questions go to **Anthropic's Claude API**.
- **The colony layer** (sharing anonymous lesson strengths across teams) doesn't exist yet. It will be opt-in and share only anonymous, generic trail strengths (§7, MEMROUTER §10).

## Retention and deletion

- **Episodes are never deleted automatically**, because they are training data (owner decision 5). The nightly sleep job **archives** old episodes into cold storage, out of retrieval.
- **`delete_memory`** takes a memory out of retrieval after you confirm. Episodes are archived; lessons and strategies are deleted, with a snapshot kept in the removal log so every removal can be traced.
- **Fear warnings** (from severe failures) are cleared only by a human, and the name is recorded.
- **Full erasure** (e.g. for a legal request): the operator runs `horizon purge-memory ID --reason … --by … --yes`. It removes the episode from live and cold storage, with its links, strength history, lesson evidence and decision-log outcomes. The purge is logged without its content, and past export files that may contain the episode are listed for the operator to rebuild. Task records (goal, decision summaries) are separate; deleting a task isn't exposed yet.

## Your controls

- `show_memories` shows everything Horizon remembers for your team, with provenance. `explain_decision` shows exactly what a decision was based on.
- `delete_memory` and `clear_fear` remove memories, with your confirmation.
- Run locally (stdio) to keep everything on your machine, and keep `HORIZON_SCORER=none` to call no third party.
