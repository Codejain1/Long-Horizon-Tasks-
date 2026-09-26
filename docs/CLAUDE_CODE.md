# Connecting Horizon to Claude Code

Horizon is an MCP server. Claude Code decides when to call it, so reliable calling comes from three layers (`PROJECT.md` §11):

1. **Directive tool descriptions**: each tool says exactly when it must be called.
2. **Instruction snippet** in the project's `CLAUDE.md` (source: `src/horizon/data/CLAUDE.snippet.md`).
3. **Hooks** that run on their own, without the model deciding to call them:

| Hook | Event | What it does |
|---|---|---|
| `horizon hook session-start` | `SessionStart` | Injects the workflow rules and lists active tasks for this project, so a resumed session continues the same task. |
| `horizon hook post-tool-use` | `PostToolUse` (matcher `Bash`) | When the command is a test run (pytest, unittest, jest, vitest, go, cargo, rspec, …), it parses the pass/fail **counts** and stores them. `record_outcome` then uses these real counts instead of the model's summary (`PROJECT.md` §9). It also nudges the model to call `record_outcome`. Raw output is never stored. |
| `horizon hook stop` | `Stop` | If tests ran for an active task but `record_outcome` wasn't called, it blocks the stop **once** and asks for it. It never loops, because it respects `stop_hook_active`. |

Hooks never break the session: any internal error exits 0 with no output. Set `HORIZON_DEBUG=1` to print errors to stderr.

## Setup

```bash
# 1. Install (Python 3.12+). The [embeddings] extra adds fastembed (BAAI/bge-small-en-v1.5).
pip install "long-horizon-platform[embeddings] @ git+https://github.com/Codejain1/Long-Horizon-Tasks-"

# 2. Wire a project: writes .mcp.json, .claude/settings.json (hooks) and the CLAUDE.md snippet.
cd /path/to/your/project
horizon install-claude-code          # --no-claude-md to skip CLAUDE.md
```

The installer is idempotent. It keeps your existing settings, hooks and MCP servers. It uses the absolute path of the current Python, so hooks work even when the virtualenv isn't on `PATH`.

**One-time approval:** Claude Code asks you to approve project `.mcp.json` servers the first time you run `claude` in the project. To skip the prompt, register the server for yourself instead:

```bash
claude mcp add -s local horizon -- "$(which python)" -m horizon serve
```

Check the connection with `claude mcp list`. It should show `horizon: … √ Connected`.

## Configuration

Everything is set through environment variables (see `src/horizon/config.py`):

| Variable | Default | Notes |
|---|---|---|
| `HORIZON_DB_URL` | `sqlite:///~/.horizon/horizon.db` | Or `postgresql://…` (needs pgvector). The server and hooks must use the same value. |
| `HORIZON_EMBEDDER` | `fastembed` | Falls back to `hash` (offline, deterministic) if fastembed or its model download is unavailable. |
| `HORIZON_TEAM_ID` | `local` | Memory is isolated per team. |
| `HORIZON_PROJECT_DIR` | server's working directory | Used to match hook-captured test runs to tasks. |
| `HORIZON_DEV_API_KEY` | none | Required for `horizon serve --transport http`. |

## Measuring whether the host calls us

`horizon stats` prints tool call counts and errors, tasks by status, and `outcome_recording_rate`. That rate is the share of real test runs (captured by the hook) that were followed by a `record_outcome` call. It is the Phase 2 reliability metric (`PROJECT.md` §14: "rate of reliable MCP invocation by the host").
