# Long-Horizon

A small model (Qwen3-14B) doing long, multi-step work, with **memrouter** as its memory. This repo is the **lab**:
runner, benchmarks, judge, world model, and memory service. The owner's WhatsApp / app assistant lives in a sibling
repo — **`owner-bot`** (see `docs/OWNER_BOT.md`). Memrouter is shared **fuel** on the server (`MEMORY_URL`).

## What's in the repo

| Folder | What it is | Where it runs |
|---|---|---|
| `memrouter/` | The memory service: `memory_service.py` (HTTP API on 127.0.0.1, SQLite store, fine-tuned bge-small encoder + learned MLP ranker, Qwen memory writer; directives = standing rules and taste profile, pending until the owner confirms ones a model inferred), `failures.py` (experience: checks that failed in real outcomes, per scope, shown as warnings, with whether each warning helped), the recall engine (`fast_recall.py`, `learned_recall.py`), `dates.py`, `writer.py`, and the training scripts (`finetune_embed.py`, `locomo_data.py`) | Hermes server `/opt/embed-memory`; one fresh instance per benchmark stream; Claude Code (via `claude/`); **owner-bot** (read-only client) |
| `runner/` | The long-horizon runner `longrun.py`: plan → contract (SPEC.md) → tests (locked) → build → verify, with a watcher that runs every check and decides retry / escalate / revise spec / re-plan / stop. `stall.py` and `testparse.py` are its helpers. Talks to memrouter for rules and lessons. | Hermes server, inside podman sandboxes, against the Stockholm GPU |
| `bench/` | `backlog.py`: real multi-ticket projects (`scenarios/rental`, `scenarios/ward`) worked in one repo, hidden tests after each ticket, QA fix-up rounds (cost to working software), arms plain / runner / notes / mem. The fast benchmark: `tasks.py` (basic and house-convention task sets), `bench.py` (paired seeded streams: mem / rules / none, paired bootstrap). `preflight.py`, `tickets.py`, `web.py`. `progress.sh` (check a run over SSM) | Hermes server `/opt/longrun/...`, results in `/home/hermes/<run>` |
| `hermes/` | Server scripts that outlived Hermes: `install.sh` (the memory service), `stockholm_model.sh` (the GPU model server), `container_cleanup.sh`. The Hermes plugins were removed 2026-10-22 (in git history) | Hermes server |
| `claude/` | `embed_memory_mcp.py`: memrouter as Claude Code's memory over MCP (`.mcp.json`) | This Mac |
| `tests/` | pytest for memrouter, runner, and benches | `.venv/bin/pytest` |

## How the pieces connect

```
owner-bot (separate repo) ──MEMORY_URL──► memrouter (/add, /search, /outcome)
        │                                        ▲
        └─ BOT_MODEL_URL ──► Qwen3-14B-AWQ on vLLM (Stockholm g5, tunnel 127.0.0.1:8000)
                                        │
bench.py ─► runner/longrun.py (one job) ──┘──► podman sandbox
     │            │  before planning and each step: /search → rules, warnings, prior owner text
     │            │  (runner writes nothing back)
     └► hidden tests score the job ─► /outcome (failing checks + warning_id), /add, /rule
```

## Run

```bash
uv venv -p 3.12 && uv pip install numpy pytest     # + torch transformers for the real encoder
.venv/bin/pytest                                     # all tests use stand-in encoders; no model needed
python runner/longrun.py "build a todo CLI with tests" --model-url http://127.0.0.1:8000/v1 --workdir /tmp/lr1 \
    --memory-url http://127.0.0.1:8765 --max-minutes 10
python bench/bench.py run --out DIR --streams mem:1,none:1 --n 16 --model-url URL [--set house]
python bench/bench.py report --out DIR
bash bench/progress.sh <run-dir> [report]            # a run on the Hermes server (needs aws login)
```

## History

Up to 2026-10-18 this repo also held Horizon v1/v2: an MCP server and Claude Code hooks, a Postgres memrouter with
spreading activation, the Jev decision layer, a world model, a SWE-bench harness and the memory benchmark harnesses
(LoCoMo, LongMemEval, BEAM). None of it was running, so it was removed. All of it is in the git tag
`snapshot-pre-restructure`: `git checkout snapshot-pre-restructure -- <path>` brings any file back. `IDEAS.md` lists what in it is
worth reusing, with the evidence.
