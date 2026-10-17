# Long-Horizon

A small model (Qwen3-14B) doing long, multi-step work, with **memrouter** as its memory. Everything here is
running somewhere today; what isn't was removed (see "History" below).

## What's in the repo

| Folder | What it is | Where it runs |
|---|---|---|
| `memrouter/` | The memory service: `memory_service.py` (HTTP API on 127.0.0.1, SQLite store, fine-tuned bge-small encoder + learned MLP ranker, Qwen memory writer, standing rules), `experience.py` (episodes, surprise, credit, consolidation into strategies, lessons and fears), the recall engine (`fast_recall.py`, `learned_recall.py`), `dates.py`, `writer.py`, and the training scripts (`finetune_embed.py`, `locomo_data.py`) | Hermes server `/opt/embed-memory`; one fresh instance per benchmark stream; Claude Code (via `claude/`) |
| `runner/` | The long-horizon runner `longrun.py`: plan → contract (SPEC.md) → tests (locked) → build → verify, with a watcher that runs every check and decides retry / escalate / revise spec / re-plan / stop. `stall.py` and `testparse.py` are its helpers. Talks to memrouter for rules and lessons. | Hermes server, inside podman sandboxes, against the Stockholm GPU |
| `bench/` | The fast benchmark: `tasks.py` (basic and house-convention task sets, each with hidden tests), `bench.py` (paired seeded streams: mem / rules / none, report with a paired bootstrap), `progress.sh` (check a run over SSM) | Hermes server `/opt/longrun/...`, results in `/home/hermes/<run>` |
| `hermes/` | The Hermes agent's plugins (`embed_memory` memory provider, `rails` hard safety rails), `install.sh` for the memory service, `stockholm_model.sh` for the GPU model server | Hermes server (Mumbai, ap-south-1) |
| `claude/` | `embed_memory_mcp.py`: memrouter as Claude Code's memory over MCP (`.mcp.json`) | This Mac |
| `tests/` | pytest for all of the above | `.venv/bin/pytest` |

## How the pieces connect

```
WhatsApp ─► Hermes agent ─► embed_memory plugin ─► memrouter (/add, /search)      rails plugin guards every tool call
                     │
                     └─► model: Qwen3-14B-AWQ on vLLM (Stockholm g5, SSH tunnel to 127.0.0.1:8000)

bench.py ─► runner/longrun.py (one job) ─► Qwen3-14B ─► podman sandbox
     │            │  before planning and each step: /search → owner's standing rules + relevant advice/lessons/fears
     │            └► after each step and the job: /episode, /add (advice), /consolidate
     └► hidden tests score the job ─► owner feedback into memrouter (/episode, /add, rules the job broke)
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
`snapshot-pre-restructure`: `git checkout snapshot-pre-restructure -- <path>` brings any file back.
