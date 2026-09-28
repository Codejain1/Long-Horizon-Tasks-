# Hosting Horizon

One Horizon server can serve many teams: MCP at `/mcp` (team API keys, metered credits) and an account page at `/`. The stack is Postgres + pgvector, the server, and a nightly sleep job.

## Run it

```bash
cp .env.example .env            # set POSTGRES_PASSWORD and HORIZON_STATE_SECRET (long random strings)
docker compose up -d --build
docker compose exec horizon horizon create-team "Acme"      # prints the first API key, shown once
```

- **The server** listens on `HORIZON_PORT` (8000). Put a TLS-terminating proxy in front of it (Caddy, nginx, a cloud load balancer) and set `FORWARDED_ALLOW_IPS` so session cookies are marked Secure.
- **Postgres** isn't published outside the compose network. Data lives in the `pgdata` volume, and world-model exports in the `exports` volume.
- **The `sleep` service** runs `horizon consolidate` once a day (MEMROUTER §7): it distils lessons, archives old episodes (never deletes them), prunes weak links and writes a Parquet export.
- **Health:** `GET /healthz` checks that the database answers. The image runs as a non-root user and has a Docker `HEALTHCHECK`.
- **The embedding model** (`BAAI/bge-small-en-v1.5`, 64 MB) is fetched at build time, so the server starts without network access. If the build has no network, it falls back to an offline hash embedder and logs a warning.

`HORIZON_STATE_SECRET` signs the state carried through MCP approval round trips. Keep it stable: rotating it cancels approvals that are in flight. Every replica must share it.

## Teams, keys and credits

| Task | How |
|---|---|
| Create a team | `docker compose exec horizon horizon create-team NAME [--credits N]` (default: 1,000 free starter credits) |
| Top up | `docker compose exec horizon horizon add-credits TEAM_ID N --reason "…"` (no payment provider yet) |
| More keys, revoking | the account page at `/` (sign in with a key), or `POST /api/keys`, `DELETE /api/keys/{id}` |
| Usage, savings | the account page, or `GET /api/account` |

Credits per call: 1 for `start_task`, `recall_context` and `record_outcome`; 5 for `evaluate_options` and `submit_consequences`; inspection and approvals are free (`src/horizon/accounts.py`). When a team runs out, tools return an error and the agent carries on without Horizon.

## Connect a project to a hosted server

```bash
export HORIZON_API_KEY=hzn_...                         # in the shell you start your agent from
horizon install-claude-code --hosted https://horizon.example.com
horizon install-codex --hosted https://horizon.example.com
```

- **MCP:** the client talks to `https://…/mcp` with the key, plus an `X-Horizon-Project` header holding a hash of the project path. That's how the server matches your hooks to your tasks without seeing the path.
- **Hooks** (`horizon hook … --remote URL`) still run on your machine. They parse test output and snapshot git **locally**, and send only counts, failing test names, commit ids and the project hash to `/api/hooks/*`. The restore command they get back is `git -C . restore …`, which runs in your project.
- Users still need Horizon installed locally for the hooks (`uv pip install -e .` or `pip install` from the repo). The MCP server itself runs on your host.

## Scorers

Set `HORIZON_SCORER=jev` with `TYPESAFE_API_KEY` (or `llm` with `ANTHROPIC_API_KEY`) in `.env`. The scorer's requests contain decision summaries, never code; see [PRIVACY.md](PRIVACY.md).

## Backups and upgrades

- **Back up:** `docker compose exec postgres pg_dump -U horizon horizon > horizon.sql`, plus the `exports` volume.
- **Upgrade:** `git pull && docker compose up -d --build`. Schema changes are additive (new tables and columns are created on start); nothing is dropped.
- **Local dev without Docker:** `HORIZON_DB_URL=postgresql://… horizon serve --transport http --host 0.0.0.0` with the `[embeddings,decision,export,web]` extras.

> **Not yet verified:** the image build itself. Docker wasn't available in the build session. The Dockerfile's steps (a non-editable install with all extras, the model prefetch, starting the server, `/healthz`, `/privacy`, create-team, `consolidate`) were run in a clean Python 3.12 environment and work. See [LAUNCH.md](LAUNCH.md).
