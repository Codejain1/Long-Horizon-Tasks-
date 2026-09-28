"""The Claude Code headless agent for the benchmark (PROJECT.md §18 "Later: add Claude Code headless as a second
baseline, since that is our real host").

mini-SWE-agent can't call MCP tools, so it can't measure Horizon itself. This runs `claude -p` on each task
twice over: without Horizon (the baseline) and with Horizon installed in the task repo (the platform run),
same agent and model, so the difference is the platform (§18 rule). With Horizon, memory persists across the
tasks and repeats of a run: improvement across repeated runs is §14's headline metric.

Dry run uses a mock `claude` (no network, no model) that edits the stub repo and prints a stream-json result.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HORIZON_TOOLS = ["mcp__horizon__start_task", "mcp__horizon__recall_context", "mcp__horizon__evaluate_options",
                 "mcp__horizon__submit_consequences", "mcp__horizon__record_outcome"]
AGENT_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash"]

MOCK_CLAUDE = '''#!{python}
"""Mock `claude -p` for dry runs: fixes the stub repo and prints a stream-json result. No network."""
import json, os, pathlib, sys
repo = pathlib.Path.cwd()
module = repo / "module.py"
if module.exists():
    module.write_text(module.read_text().replace("return a - b", "return a + b"))
horizon = (repo / ".mcp.json").exists()
print(json.dumps({{"type": "system", "subtype": "init", "mcp_servers": [{{"name": "horizon"}}] if horizon else []}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "num_turns": 6 if horizon else 5,
                  "total_cost_usd": 0.042 if horizon else 0.038, "duration_ms": 1200,
                  "usage": {{"input_tokens": 1200, "output_tokens": 400, "cache_creation_input_tokens": 300,
                            "cache_read_input_tokens": 9000}}}}))
'''


def mock_claude(workdir: Path) -> str:
    path = workdir / "mock-claude"
    path.write_text(MOCK_CLAUDE.format(python=sys.executable))
    path.chmod(0o755)
    return str(path)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=bench", "-c", "user.email=bench@example.invalid", *args], cwd=repo,
                          check=True, capture_output=True, text=True, timeout=600).stdout


def install_horizon(repo: Path) -> None:
    """Horizon in the task repo, committed as setup so the evaluated patch contains only the agent's changes."""
    from horizon.install import install

    install(repo, command=[sys.executable, "-m", "horizon"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "bench: install horizon", "--allow-empty")


def patch_since(repo: Path, base: str) -> str:
    _git(repo, "add", "-A")
    return _git(repo, "diff", "--cached", base, "--", ".", ":(exclude).horizon", ":(exclude)**/__pycache__")


def run_claude(problem: str, repo: Path, *, claude: str, model: str, horizon: bool, max_turns: int,
               max_budget_usd: float, env: dict, timeout_s: int = 3600) -> tuple[dict, list[dict]]:
    """Runs one headless session. Returns (the stream's result event, all events)."""
    cmd = [claude, "-p", problem, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
           "--model", model.split("/")[-1], "--max-turns", str(max_turns), "--max-budget-usd", str(max_budget_usd),
           # Only the task repo's settings and (with Horizon) its MCP server: the machine's own setup can't leak in.
           "--setting-sources", "project,local", "--strict-mcp-config",
           "--allowedTools", *AGENT_TOOLS, *(HORIZON_TOOLS if horizon else [])]
    if horizon:
        cmd += ["--mcp-config", ".mcp.json"]
    proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout_s,
                          stdin=subprocess.DEVNULL)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None:
        raise RuntimeError(f"claude exited {proc.returncode} without a result: {proc.stderr[-1000:]}")
    return result, events


def run_task(instance: dict, cfg, run_dir: Path, env_kind: str, meta: dict, *, horizon: bool,
             memory_db: Path) -> dict:
    from horizon.bench.runner import RESULT_SCHEMA_VERSION, make_stub_repo, prepare_local_checkout

    iid = instance["instance_id"]
    workdir = run_dir / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION, "run_id": meta["run_id"], "stage": meta["stage"],
        "repeat": meta["repeat"], "instance_id": iid, "repo": instance.get("repo"),
        "difficulty": instance.get("difficulty"), "model": cfg.model.name, "dry_run": cfg.dry_run,
        "agent": "claude-code", "horizon": horizon, "environment": env_kind, "step_limit": cfg.limits.step_limit,
        "cost_limit_usd": cfg.limits.cost_limit_usd, "exit_status": None, "submitted": False, "steps": 0,
        "tokens": {"total": 0}, "cost_usd": 0.0, "wall_time_s": 0.0, "patch_chars": 0, "resolved": None, "error": None,
    }
    start, patch = time.monotonic(), ""
    try:
        if env_kind == "stub":
            repo, setup = make_stub_repo(instance, workdir), {}
        else:
            repo, setup = prepare_local_checkout(instance, workdir)
        record["env_setup"] = setup
        if horizon:
            install_horizon(repo)
        base = _git(repo, "rev-parse", "HEAD").strip()
        env = {**os.environ, "HORIZON_DB_URL": f"sqlite:///{memory_db}", "HORIZON_PROJECT_DIR": str(repo)}
        env.pop("CLAUDE_PROJECT_DIR", None)
        venv_bin = repo / ".venv-bench" / "bin"
        if venv_bin.exists():
            env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
        claude = mock_claude(workdir) if cfg.dry_run else os.environ.get("HORIZON_BENCH_CLAUDE", "claude")
        result, events = run_claude(instance["problem_statement"], repo, claude=claude, model=cfg.model.name,
                                    horizon=horizon, max_turns=cfg.limits.step_limit,
                                    max_budget_usd=cfg.limits.cost_limit_usd, env=env)
        usage = result.get("usage") or {}
        tokens = {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0),
                  "cache_write": usage.get("cache_creation_input_tokens", 0),
                  "cache_read": usage.get("cache_read_input_tokens", 0)}
        record.update(exit_status="Submitted" if result.get("subtype") == "success" else result.get("subtype"),
                      steps=int(result.get("num_turns") or 0), cost_usd=round(float(result.get("total_cost_usd") or 0), 6),
                      tokens=tokens | {"total": sum(tokens.values())},
                      horizon_calls=sum(1 for e in events if e.get("type") == "assistant"
                                        for b in (e.get("message") or {}).get("content") or []
                                        if isinstance(b, dict) and b.get("type") == "tool_use"
                                        and str(b.get("name", "")).startswith("mcp__horizon__")))
        patch = patch_since(repo, base)
        (run_dir / "trajectories").mkdir(exist_ok=True)
        (run_dir / "trajectories" / f"{iid}.claude.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    except Exception as exc:  # one broken task must not stop the run
        record["exit_status"] = type(exc).__name__
        record["error"] = f"{exc}"[:2000]
    finally:
        record["wall_time_s"] = round(time.monotonic() - start, 3)
        record["submitted"] = record["exit_status"] == "Submitted" and bool(patch.strip())
        record["patch_chars"] = len(patch)
    return record | {"_patch": patch}


def check_ready() -> None:
    claude = os.environ.get("HORIZON_BENCH_CLAUDE", "claude")
    try:
        subprocess.run([claude, "--version"], check=True, capture_output=True, timeout=30)
    except Exception as exc:
        raise SystemExit(f"real runs with agent claude-code need the `claude` CLI logged in ({shlex.quote(claude)}): "
                         f"{exc}") from exc
