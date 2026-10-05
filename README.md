# Horizon

**Memory that carries across sessions, projects and teammates, for coding agents.** Horizon plugs into Claude Code through hooks. It captures what you tell your agent and what actually happens when tests run. Then every later session, in any project and on any teammate's machine, starts with what it needs to know. The agent doesn't have to call anything.

Your agent still does all the coding with your own subscription. Horizon never needs your code: see [Data and privacy](docs/PRIVACY.md).

## What it does (the default, lean profile)

- **Team rules:** say once "for every service we build, ids are uuid4 hex", and every later session in every project of the team is told. Hosted, that includes your teammates' sessions.
- **Project continuity:** each session's goal and constraints are shown to the next one, so work spread over days doesn't drift.
- **Recurring pitfalls:** checks that failed in several earlier sessions are flagged up front ("get them right the first time"). This is learned from real test runs, including `./ci.sh`-style wrappers.
- **Rules picked out by [Jev](https://typesafe.ai)** when configured (97 % on our labelled set, against 62 % for keywords), with keywords as the fallback.

## Does it help? (`evals/longhorizon/RESULTS.md`)

Each scenario is a backlog of tickets, one fresh unattended session per ticket. Rules are given in the early tickets and needed later, and hidden tests check the result.

| Measured (Claude Code + Sonnet) | Claude Code alone | With Horizon |
|---|---|---|
| Rules from earlier tickets kept (ward and rental, 4 runs) | 7/40 | **40/40** |
| Hidden tests passed (same runs) | 73/108 | **106/108** |
| Cost and time to build (2 runs each, with the project map) | $3.11, 15.6 min | **$3.07, 14.5 min** |
| Cost and time to *working* software (build + QA fix-ups, 4 runs) | $8.71, 46.4 min; 10 checks still failing; a person had to restate the rules | **$7.42, 38.8 min; 1 still failing** |
| Team rules carried into new projects (3 repeats) | 15/24 hidden checks | **24/24** |
| A CI policy kept breaking across sessions (3 replays) | broken in 7 of 15 sessions | **0 of 15, 12 % cheaper** |
| One interactive session, or rules already written in the repo | – | no gain, so don't expect one |

**At the same cost as the agent alone, it keeps the rules you gave it.** The agent alone looks cheap only until its forgotten rules are found: it can't fix them from the failures, because nothing in the repo records them, so someone has to explain them again. Horizon helps most where no human carries the context: **unattended agents and teams.**

The **full profile** (`--profile full`) adds the original workflow: decision scoring with Jev, consequence checks and spikes, recall before each step, outcomes per test run, and rollback. In our evals it didn't improve outcomes and cost more, so it's opt-in.

## Set up in under 5 minutes

You need Python 3.12+, [uv](https://docs.astral.sh/uv/) (or pip), git, and Claude Code or Codex. A cold setup takes about a minute; a one-time 64 MB model download happens on first use.

```bash
# 1. Install Horizon (the repo is private until launch: you need access to clone it)
git clone https://github.com/Codejain1/Long-Horizon-Tasks- horizon && cd horizon
uv venv -p 3.12 && uv pip install -e ".[embeddings]"

# 2. Wire it into your project (pick your agent)
cd /path/to/your/project
/path/to/horizon/.venv/bin/horizon install-claude-code     # Claude Code (lean: hooks do the work)
/path/to/horizon/.venv/bin/horizon install-codex           # Codex (the full workflow; lean isn't supported there yet)
```

**3. Start your agent in the project.**
- **Claude Code:** run `claude`, then approve the `horizon` server once when asked. `claude mcp list` should show `horizon … ✓ Connected`.
- **Codex:** run `codex`, then trust the project and its hooks once (`/hooks`). `codex mcp list` should show `horizon`.

**4. Give it a task as usual.** With the default lean profile, the hooks capture the task, rules and test runs, and the agent doesn't call Horizon. With `--profile full` (and in Codex), the installed instructions tell the agent when to call Horizon's tools. Check it's working with:

```bash
/path/to/horizon/.venv/bin/horizon stats
```

That's the whole setup. Everything runs locally, in a SQLite file at `~/.horizon/horizon.db`, and nothing leaves your machine.

### What the installers write

| | Claude Code (`install-claude-code`) | Codex (`install-codex`) |
|---|---|---|
| MCP server | `.mcp.json` (full profile, or lean with `--mcp`) | `.codex/config.toml` (`[mcp_servers.horizon]`, tools pre-approved) |
| Hooks | `.claude/settings.json` (lean adds `UserPromptSubmit`) | `.codex/hooks.json` (same format) |
| Instructions | a snippet in `CLAUDE.md` | the same snippet in `AGENTS.md` |
| Spike scratch space | `.horizon/` added to `.gitignore` | same |

Both installers are idempotent and keep your existing settings. The snippet lives between `<!-- horizon:start -->` and `<!-- horizon:end -->`: see [`src/horizon/data/agent.snippet.md`](src/horizon/data/agent.snippet.md).

## Optional: Jev

Horizon works without a scorer. With Jev, the lean profile picks out rules from your messages much more accurately (97 % against 62 % for keywords), and the full profile scores decisions. To enable it:

```bash
export HORIZON_SCORER=jev TYPESAFE_API_KEY=...        # Jev by TypeSafe (console.typesafe.ai)
# or: HORIZON_SCORER=llm with Claude API credentials   # the small-LLM comparison scorer
uv pip install -e ".[embeddings,decision]"
```

Set these in the environment you start your agent from, because the hooks run there. Enabling a scorer sends message sentences and decision summaries (code removed) to that provider; see [privacy](docs/PRIVACY.md#who-else-sees-it).

## Hosted mode

Teams can run one shared Horizon: `docker compose up` gives you Postgres, the server, an account page with API keys, credits, usage and savings per session, and the nightly sleep job. Connect a project with:

```bash
export HORIZON_API_KEY=hzn_...        # from `horizon create-team` or the account page
horizon install-claude-code --hosted https://horizon.example.com    # lean: team rules reach every teammate
```

Hooks still run on your machine and send only parsed facts (test counts, failing test names, commit ids, a hashed project key) and, in lean, your messages with code removed. See [docs/HOSTING.md](docs/HOSTING.md).

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
.venv/bin/horizon-bench run --stage smoke --agent claude-code --with-horizon   # Horizon itself, dry run
demo/reliability/run.sh /tmp/rel1                                   # does a real Claude Code call the tools?
```

`tests/test_e2e.py` runs the whole flow against the hosted server over real HTTP, from start to account page. MIT licensed.
