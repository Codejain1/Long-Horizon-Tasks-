"""Run mini-SWE-agent over the task list and record one JSON line per task run.

Environments:
- ``stub``   (dry run): a tiny local git repo per task. No network.
- ``docker``: the task's SWE-bench image (mini-SWE-agent's standard setup).
- ``local``:  fallback when Docker isn't available: clone the repo at its base commit and
  install it into a venv (best effort; can differ from the evaluation environment).

The per-task record (``results.jsonl``, schema version 1) is also the start of the
world-model training log (PROJECT.md §6: "log data in a trainable format from day one").
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from horizon.bench.config import REPO_ROOT, BenchConfig
from horizon.bench.models import MockModel, Usage, make_priced_litellm_model, message_usage, mock_behaviour
from horizon.bench.select import select_tasks, synthetic_verified_like

RESULT_SCHEMA_VERSION = 1


# --- task list and instances -------------------------------------------------------------------

def load_task_list(cfg: BenchConfig) -> tuple[list[dict], str]:
    """The committed task list; in dry run without one, a synthetic list with the same rules."""
    path = cfg.path(cfg.selection.task_list)
    if path.exists():
        shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
        return json.loads(path.read_text())["tasks"], str(shown)
    if cfg.real_runs:
        raise SystemExit(f"No task list at {path}. Run `horizon-bench select` first (needs huggingface.co).")
    s = cfg.selection
    return select_tasks(synthetic_verified_like(), seed=s.seed, n_tasks=s.n_tasks, smoke_n=s.smoke_n,
                        take_all=s.take_all, weights=s.weights), "synthetic"


def stage_tasks(tasks: list[dict], stage: str, smoke_n: int) -> list[dict]:
    if stage == "smoke":
        return tasks[:smoke_n]
    if stage == "full":
        return tasks
    raise ValueError(f"unknown stage {stage!r} (smoke | full)")


def load_instances(cfg: BenchConfig, tasks: list[dict], source: str) -> dict[str, dict]:
    ids = {t["instance_id"] for t in tasks}
    if cfg.real_runs:
        from datasets import load_dataset  # needs huggingface.co

        rows = load_dataset(cfg.dataset.name, split=cfg.dataset.split)
        found = {r["instance_id"]: dict(r) for r in rows if r["instance_id"] in ids}
        missing = ids - found.keys()
        if missing:
            raise SystemExit(f"{len(missing)} task ids not in {cfg.dataset.name}: {sorted(missing)[:3]}…")
        return found
    if source == "synthetic":
        return {i["instance_id"]: i for i in synthetic_verified_like() if i["instance_id"] in ids}
    # Dry run over the real task list: no dataset access, so use placeholder problem statements.
    return {t["instance_id"]: {**t, "problem_statement": f"[dry run] {t['instance_id']}"} for t in tasks}


# --- environments ------------------------------------------------------------------------------

def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve_environment(cfg: BenchConfig) -> str:
    if cfg.dry_run:
        return "stub"
    choice = cfg.execution.environment
    if choice == "auto":
        return "docker" if docker_available() else "local"
    if choice not in ("docker", "local"):
        raise ValueError(f"unknown environment {choice!r} (auto | docker | local)")
    return choice


def _git(args: list[str], cwd: Path, timeout: int = 600) -> None:
    subprocess.run(["git", "-c", "user.name=bench", "-c", "user.email=bench@example.invalid", *args],
                   cwd=cwd, check=True, capture_output=True, timeout=timeout)


def make_stub_repo(instance: dict, root: Path) -> Path:
    repo = root / instance["instance_id"]
    repo.mkdir(parents=True)
    (repo / "README.md").write_text(f"# {instance['repo']} (dry-run stub)\n")
    (repo / "module.py").write_text("def add(a, b):\n    return a - b\n")
    _git(["init", "-q"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "stub"], repo)
    return repo


def prepare_local_checkout(instance: dict, root: Path, install: bool = True) -> tuple[Path, dict]:
    """Clone the task repo at its base commit and (best effort) install it into a venv."""
    base = os.environ.get("HORIZON_BENCH_GIT_BASE", "https://github.com")
    repo = root / instance["instance_id"]
    log: dict[str, Any] = {"cloned": False, "installed": None}
    subprocess.run(["git", "clone", "--quiet", f"{base}/{instance['repo']}.git", str(repo)], check=True,
                   capture_output=True, timeout=1800)
    _git(["checkout", "--quiet", instance["base_commit"]], repo)
    log["cloned"] = True
    if install:
        venv = repo / ".venv-bench"
        subprocess.run(["python3", "-m", "venv", str(venv)], check=True, capture_output=True, timeout=300)
        proc = subprocess.run([str(venv / "bin" / "pip"), "install", "-q", "-e", ".", "pytest"], cwd=repo,
                              capture_output=True, text=True, timeout=1800)
        log["installed"] = proc.returncode == 0
        if proc.returncode != 0:
            log["install_error"] = proc.stderr[-2000:]
    return repo, log


def base_mini_config() -> dict:
    from minisweagent.config import builtin_config_dir

    return yaml.safe_load((builtin_config_dir / "benchmarks" / "swebench.yaml").read_text())


def make_environment(kind: str, instance: dict, workdir: Path, base: dict, cfg: BenchConfig):
    """Returns (environment, working directory inside it, setup log)."""
    from minisweagent.environments.local import LocalEnvironment

    timeout = cfg.limits.command_timeout_s
    env_vars = {k: v for k, v in base["environment"].get("env", {}).items() if k != "BASH_ENV"}
    if kind == "stub":
        repo = make_stub_repo(instance, workdir)
        return LocalEnvironment(cwd=str(repo), timeout=timeout, env=env_vars), str(repo), {}
    if kind == "local":
        repo, log = prepare_local_checkout(instance, workdir)
        venv_bin = repo / ".venv-bench" / "bin"
        env_vars = {**env_vars, "PATH": f"{venv_bin}:{os.environ.get('PATH', '')}", "VIRTUAL_ENV": str(venv_bin.parent)}
        return LocalEnvironment(cwd=str(repo), timeout=timeout, env=env_vars), str(repo), log
    if kind == "docker":
        from minisweagent.run.benchmarks.swebench import get_sb_environment

        env_cfg = {**base["environment"], "environment_class": "docker", "timeout": timeout}
        return get_sb_environment({"environment": env_cfg}, instance), env_cfg.get("cwd", "/testbed"), {}
    raise ValueError(kind)


# --- one task ----------------------------------------------------------------------------------

def make_model(cfg: BenchConfig, instance_id: str, base: dict):
    if cfg.dry_run:
        return MockModel(cfg.model.prices, behaviour=mock_behaviour(instance_id))
    return make_priced_litellm_model(cfg.model.name, cfg.model.prices, base.get("model", {}))


def run_task(instance: dict, cfg: BenchConfig, run_dir: Path, env_kind: str, base: dict, meta: dict) -> dict:
    from minisweagent.agents.default import DefaultAgent

    iid = instance["instance_id"]
    workdir = run_dir / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION, "run_id": meta["run_id"], "stage": meta["stage"],
        "repeat": meta["repeat"], "instance_id": iid, "repo": instance.get("repo"),
        "difficulty": instance.get("difficulty"), "model": cfg.model.name, "dry_run": cfg.dry_run,
        "environment": env_kind, "step_limit": cfg.limits.step_limit, "cost_limit_usd": cfg.limits.cost_limit_usd,
        "exit_status": None, "submitted": False, "steps": 0, "tokens": Usage().__dict__ | {"total": 0},
        "cost_usd": 0.0, "wall_time_s": 0.0, "patch_chars": 0, "resolved": None, "error": None,
    }
    start = time.monotonic()
    agent = None
    patch = ""
    try:
        env, cwd, setup = make_environment(env_kind, instance, workdir, base, cfg)
        record["env_setup"] = setup
        agent_cfg = {**base["agent"], "step_limit": cfg.limits.step_limit, "cost_limit": cfg.limits.cost_limit_usd}
        if cwd != "/testbed":
            for key in ("system_template", "instance_template"):
                agent_cfg[key] = agent_cfg[key].replace("/testbed", cwd)
        agent = DefaultAgent(make_model(cfg, iid, base), env, **agent_cfg)
        info = agent.run(instance["problem_statement"])
        record["exit_status"] = info.get("exit_status")
        patch = info.get("submission") or ""
    except Exception as exc:  # one broken task must not stop the run
        record["exit_status"] = type(exc).__name__
        record["error"] = f"{exc}"[:2000]
        record["traceback"] = traceback.format_exc()[-4000:]
    finally:
        record["wall_time_s"] = round(time.monotonic() - start, 3)
        if agent is not None:
            usage = sum((message_usage(m) for m in agent.messages if m.get("role") == "assistant"), Usage())
            record.update(steps=agent.n_calls, cost_usd=round(agent.cost, 6),
                          tokens=usage.__dict__ | {"total": usage.total})
            litellm_costs = [m["extra"].get("litellm_cost") for m in agent.messages
                             if m.get("role") == "assistant" and "litellm_cost" in m.get("extra", {})]
            if litellm_costs:
                record["litellm_cost_usd"] = (round(sum(litellm_costs), 6)
                                              if all(c is not None for c in litellm_costs) else None)
            agent.save(run_dir / "trajectories" / f"{iid}.traj.json")
        record["submitted"] = record["exit_status"] == "Submitted"
        record["patch_chars"] = len(patch)
        if env_kind == "stub":
            shutil.rmtree(workdir / iid, ignore_errors=True)
    return record | {"_patch": patch}


# --- a run -------------------------------------------------------------------------------------

def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return None


def run(cfg: BenchConfig, stage: str, repeats: int | None = None, out_root: Path | None = None,
        limit: int | None = None) -> list[Path]:
    """Run the stage ``repeats`` times; returns one run directory per repeat."""
    if cfg.real_runs:
        cfg.check_real_run_ready()
    tasks, source = load_task_list(cfg)
    tasks = stage_tasks(tasks, stage, cfg.selection.smoke_n)[:limit]
    instances = load_instances(cfg, tasks, source)
    env_kind = resolve_environment(cfg)
    base = base_mini_config()
    out_root = out_root or cfg.path(cfg.output_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dirs = []
    for repeat in range(1, (repeats or 1) + 1):
        run_id = f"{stamp}-{stage}-{'dry' if cfg.dry_run else 'real'}-r{repeat}"
        run_dir = out_root / run_id
        run_dir.mkdir(parents=True)
        meta = {"run_id": run_id, "stage": stage, "repeat": repeat, "dry_run": cfg.dry_run,
                "environment": env_kind, "task_list": source, "n_tasks": len(tasks), "git_sha": git_sha(),
                "started_at": datetime.now(UTC).isoformat(), "config": cfg.model_dump()}
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        preds: dict[str, dict] = {}
        with (run_dir / "results.jsonl").open("w") as out:
            for task in tasks:
                rec = run_task(instances[task["instance_id"]], cfg, run_dir, env_kind, base, meta)
                patch = rec.pop("_patch")
                preds[rec["instance_id"]] = {"instance_id": rec["instance_id"], "model_name_or_path": cfg.model.name,
                                             "model_patch": patch}
                out.write(json.dumps(rec) + "\n")
                out.flush()
        (run_dir / "preds.json").write_text(json.dumps(preds, indent=2))
        shutil.rmtree(run_dir / "work", ignore_errors=True)
        run_dirs.append(run_dir)
    return run_dirs


def read_results(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines() if line.strip()]
