# Launch checklist

`PROJECT.md` §13, item 8: *publish repo, benchmarks and write-up.* This page lists what's ready and what's waiting, and for what.

## Ready

- [x] **Phases 1–7 built and tested:** 367 tests on SQLite and Postgres 14 + pgvector.
- [x] **Setup in under 5 minutes:** measured from a fresh clone (≈ 1 min cold: clone 5 s, install 25 s, the 64 MB model 26 s, wiring 3 s), plus Claude Code's one-time server approval.
- [x] **Claude Code:** `horizon install-claude-code`; verified in 50+ real headless sessions (Opus 5.5, Sonnet 5), see `demo/reliability/RESULTS.md`.
- [x] **Codex:** `horizon install-codex`; verified in a real `codex exec` session (tools called, hooks fired, baseline and checkpoints worked).
- [x] **Instruction snippets:** CLAUDE.md and AGENTS.md (one shared text: `src/horizon/data/agent.snippet.md`).
- [x] **Data and privacy policy:** `docs/PRIVACY.md`, also served at `/privacy`. No raw code is stored; every storage path was checked, see PROGRESS session 11.
- [x] **Hosted mode:** Docker Compose (Postgres, the server, the nightly sleep job), team API keys, credits, the account page, and the hosted hook bridge.
- [x] **End-to-end test** of the full flow over real HTTP: `tests/test_e2e.py`.
- [x] **Jev verified live:** decisions, consequence re-scoring and the attention filter.

## Waiting on the owner

| Item | Why it waits | What's needed |
|---|---|---|
| **Make the repo public** | Hard to reverse (CLAUDE.md decision rule) | Your go-ahead. Check the git history is clean of secrets first: no keys were ever committed, and every session checked. |
| **Real benchmark runs** (Phase 1 baseline: smoke run, then 50 × 3) | Costs money | `ANTHROPIC_API_KEY` and `SWEBENCH_API_KEY`, then `real_runs: true` in `bench/config.yaml`. Worst case about $160. |
| **A platform benchmark** (the agent *with* Horizon) | mini-SWE-agent doesn't speak MCP (open question 10), and the §18 second baseline, Claude Code headless, isn't built yet | Decide whether Claude Code headless becomes the headline benchmark (§18 "later"). It would run on a subscription, but needs Docker for SWE-bench environments. |
| **The write-up** | Needs the benchmark numbers | The benchmark runs above. |
| **Hosting provider** | §17: decided at launch | A provider and a domain. The compose file is provider-neutral. |
| **Pricing, free tier, payments** | §15, open question 48 | Prices (the placeholders are in `accounts.py`), and a payment provider if any. |
| **Build and run the Docker image once** | Docker wasn't available in the build session | `docker compose up --build` on any machine with Docker. The same steps already passed in a clean Python environment. |
| **Tracing (Langfuse)** | §10 names an existing tracing tool; that needs an account | A Langfuse project and keys, if wanted. Until then, `tool_calls`, the decision log and `horizon stats` cover internal debugging. |
| **Privacy hard-delete** | Open question 47: it conflicts with "episodes are never deleted" | A decision on legal-request erasure. |

## Launch-day steps (once the above are done)

1. Merge the Phase 8 PR, and tag `v1.0.0`.
2. Run the benchmark (baseline, then with Horizon, 3 repeats each), then publish `bench/runs/*/report.md` and the write-up.
3. Make the repo public, which makes `pip install git+https://…` and the README's clone command work for everyone.
4. Deploy the compose stack, create the first teams, and point `horizon install-* --hosted` at it.
