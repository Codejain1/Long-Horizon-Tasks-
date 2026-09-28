# Horizon

**Experience for coding agents.** Horizon is an MCP server that plugs into Claude Code or Codex. It keeps long tasks on track, scores crucial decisions before your agent commits to them, rolls back cleanly when a change breaks something, and remembers what actually worked, so the next task starts smarter.

> Other tools give agents a better notebook. Horizon gives them experience.

Your agent still does all the coding with your own subscription. Horizon decides, remembers and learns. It never needs your code: see [Data and privacy](docs/PRIVACY.md).

## What it does

- **Task state:** the goal verbatim, constraints, plan, decisions and progress, sent back to the agent at every step so it doesn't drift.
- **Decisions:** at a crucial choice (framework, database, architecture), `evaluate_options` scores the options on success, compatibility, fit and cost. It uses [Jev](https://typesafe.ai) or a small LLM, plus your estimates.
- **Consequence checks:** close calls get cheap checks, then small throwaway spikes built on your machine. Past results are reused instead of re-spiking.
- **Rollback:** before each decision, hooks snapshot git. When a change breaks tests that used to pass, the agent is told to restore and retry differently. After 3 failed attempts it asks you. Tests that were already failing when the task started don't count against it.
- **Memory that learns:** outcomes strengthen or weaken what's remembered, a nightly sleep job distils lessons and strategies, and severe failures become warnings that always surface.
- **You stay in charge:** high-stakes ties, clearing a warning and deleting a memory are asked of **you** in the client. The agent can't approve on your behalf. `explain_decision` and `show_memories` show why and what.

## Set up in under 5 minutes

You need Python 3.12+, [uv](https://docs.astral.sh/uv/) (or pip), git, and Claude Code or Codex. A cold setup takes about a minute; a one-time 64 MB model download happens on first use.

```bash
# 1. Install Horizon (the repo is private until launch: you need access to clone it)
git clone https://github.com/Codejain1/Long-Horizon-Tasks- horizon && cd horizon
uv venv -p 3.12 && uv pip install -e ".[embeddings]"

# 2. Wire it into your project (pick your agent)
cd /path/to/your/project
/path/to/horizon/.venv/bin/horizon install-claude-code     # Claude Code
/path/to/horizon/.venv/bin/horizon install-codex           # Codex
```

**3. Start your agent in the project.**
- **Claude Code:** run `claude`, then approve the `horizon` server once when asked. `claude mcp list` should show `horizon … ✓ Connected`.
- **Codex:** run `codex`, then trust the project and its hooks once (`/hooks`). `codex mcp list` should show `horizon`.

**4. Give it a task as usual.** The installed instructions tell the agent when to call Horizon, and the hooks capture real test results, checkpoints and missing outcomes on their own. Check it's working with:

```bash
/path/to/horizon/.venv/bin/horizon stats
```

That's the whole setup. Everything runs locally, in a SQLite file at `~/.horizon/horizon.db`, and nothing leaves your machine.

### What the installers write

| | Claude Code (`install-claude-code`) | Codex (`install-codex`) |
|---|---|---|
| MCP server | `.mcp.json` | `.codex/config.toml` (`[mcp_servers.horizon]`, tools pre-approved) |
| Hooks | `.claude/settings.json` | `.codex/hooks.json` (same format) |
| Instructions | a snippet in `CLAUDE.md` | the same snippet in `AGENTS.md` |
| Spike scratch space | `.horizon/` added to `.gitignore` | same |

Both installers are idempotent and keep your existing settings. The snippet lives between `<!-- horizon:start -->` and `<!-- horizon:end -->`: see [`src/horizon/data/agent.snippet.md`](src/horizon/data/agent.snippet.md).

## Optional: score decisions with Jev

Horizon works without a scorer: decisions come back `unscored`, and the rest still works. To score them:

```bash
export HORIZON_SCORER=jev TYPESAFE_API_KEY=...        # Jev by TypeSafe (console.typesafe.ai)
# or: HORIZON_SCORER=llm with Claude API credentials   # the small-LLM comparison scorer
uv pip install -e ".[embeddings,decision]"
```

Set these in the environment you start your agent from. Enabling a scorer sends decision summaries (never code) to that provider; see [privacy](docs/PRIVACY.md#who-else-sees-it).

## Hosted mode

Teams can run one shared Horizon: `docker compose up` gives you Postgres, the server, an account page with API keys, credits, usage and savings per session, and the nightly sleep job. Connect a project with:

```bash
export HORIZON_API_KEY=hzn_...        # from `horizon create-team` or the account page
horizon install-claude-code --hosted https://horizon.example.com    # or install-codex --hosted …
```

Hooks still run on your machine and send only parsed facts (test counts, failing test names, commit ids, a hashed project key). See [docs/HOSTING.md](docs/HOSTING.md).

## Documentation

- [Claude Code](docs/CLAUDE_CODE.md): hooks, configuration, the decision layer, rollback, memory, inspection and approvals.
- [Codex](docs/CODEX.md): the same for Codex, and what differs.
- [Hosting](docs/HOSTING.md): Docker, accounts, credits, backups and upgrades.
- [Data and privacy](docs/PRIVACY.md): exactly what is stored, where, and who sees it.
- [Launch checklist](docs/LAUNCH.md): what's done and what's pending.
- Design: [PROJECT.md](docs/PROJECT.md) (the source of truth), [MEMROUTER.md](docs/MEMROUTER.md) (memory). Build history: [PROGRESS.md](PROGRESS.md).

## Development

```bash
uv venv -p 3.12 && uv pip install -e ".[dev,embeddings,bench,decision,export,web]"
.venv/bin/pytest                                                   # SQLite
HORIZON_TEST_PG_URL=postgresql://user:pass@localhost/db .venv/bin/pytest   # plus Postgres + pgvector
.venv/bin/horizon-bench run --stage smoke                          # benchmark harness, dry run
demo/reliability/run.sh /tmp/rel1                                   # does a real Claude Code call the tools?
```

`tests/test_e2e.py` runs the whole flow against the hosted server over real HTTP, from start to account page. MIT licensed.
