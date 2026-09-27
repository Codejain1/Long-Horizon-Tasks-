# Long-Horizon Tasks

An MCP server that gives coding agents task state and outcome-learning memory. See `docs/PROJECT.md` (source of truth) and `docs/MEMROUTER.md` (memory spec). Build status is in `PROGRESS.md`.

## What's here

**Phase 1 — benchmark harness** (`src/horizon/bench/`, `bench/`): mini-SWE-agent + `claude-sonnet-5` on 50 fixed SWE-bench Verified tasks. Dry run by default; see `bench/README.md`.

**Phase 2 — MCP server and basic memory:**

- **MCP tools:** `start_task`, `recall_context` and `record_outcome` (`src/horizon/server.py`), over stdio or streamable HTTP with a dev API key.
- **Task state** (`src/horizon/taskstate/`): goal (verbatim), constraints, plan, progress, decisions and open issues.
- **Memrouter step 1** (`src/horizon/memrouter/`): episodes, the write path with surprise scoring, and basic similarity recall.
- **Claude Code integration:** hooks, a CLAUDE.md snippet and an installer. See `docs/CLAUDE_CODE.md`.
- **Storage:** SQLite for zero-setup local use, or Postgres + pgvector.

**Phase 4 — decision layer** (`src/horizon/decision/`): the `evaluate_options` tool scores the host's options for a crucial choice. It uses Jev (or the small-LLM comparison scorer) plus the host's cost estimates, with weights and thresholds in code. See `docs/CLAUDE_CODE.md`.

**Phase 3 — checkpoints and rollback** (`src/horizon/taskstate/checkpoints.py`, `rollback.py`): a hook records a git and Claude Code checkpoint reference before each decision. A failed outcome returns a rollback (restore, the failure reasons fed back, a retry limit), and the task is escalated to the user after the limit. See `docs/CLAUDE_CODE.md`.

## Quick start

```bash
uv venv -p 3.12 && uv pip install -e ".[dev,embeddings,bench,decision]"
.venv/bin/pytest                                   # SQLite tests
HORIZON_TEST_PG_URL=postgresql://user:pass@localhost/db .venv/bin/pytest   # also Postgres + pgvector

.venv/bin/horizon install-claude-code --dir /path/to/project   # wire into Claude Code
.venv/bin/horizon stats                                        # invocation reliability
HORIZON_DEV_API_KEY=change-me docker compose up --build        # Postgres + HTTP server
.venv/bin/horizon-bench run --stage smoke                        # Phase 1 harness, dry run
demo/reliability/run.sh /tmp/rel1                              # host reliability demo (real Claude Code)
```
