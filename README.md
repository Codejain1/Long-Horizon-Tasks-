# Long-Horizon Tasks

An MCP server that gives coding agents task state and outcome-learning memory. See `docs/PROJECT.md` (source of truth) and `docs/MEMROUTER.md` (memory spec). Build status is in `PROGRESS.md`.

## What's here (Phase 2)

- **MCP tools:** `start_task`, `recall_context` and `record_outcome` (`src/horizon/server.py`), over stdio or streamable HTTP with a dev API key.
- **Task state** (`src/horizon/taskstate/`): goal (verbatim), constraints, plan, progress, decisions and open issues.
- **Memrouter step 1** (`src/horizon/memrouter/`): episodes, the write path with surprise scoring, and basic similarity recall.
- **Claude Code integration:** hooks, a CLAUDE.md snippet and an installer. See `docs/CLAUDE_CODE.md`.
- **Storage:** SQLite for zero-setup local use, or Postgres + pgvector.

## Quick start

```bash
uv venv -p 3.12 && uv pip install -e ".[dev,embeddings]"
.venv/bin/pytest                                   # SQLite tests
HORIZON_TEST_PG_URL=postgresql://user:pass@localhost/db .venv/bin/pytest   # also Postgres + pgvector

.venv/bin/horizon install-claude-code --dir /path/to/project   # wire into Claude Code
.venv/bin/horizon stats                                        # invocation reliability
HORIZON_DEV_API_KEY=change-me docker compose up --build        # Postgres + HTTP server
```
