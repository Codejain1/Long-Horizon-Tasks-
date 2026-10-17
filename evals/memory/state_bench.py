"""STATE-Bench wrapper (microsoft/STATE-Bench) — agent simulator, not QA-json.

Requires a separate checkout with protocol eval credentials. This script clones the repo and,
when configured, runs a small travel-domain batch and records pass@1.

    export STATE_BENCH_ROOT=~/.cache/horizon-bench/STATE-Bench
    export RUN_STATE_BENCH=1
    # Configure locked simulator/judge per STATE-Bench docs (Azure GPT-5.4 eval client).
    python evals/memory/state_bench.py --install
    python evals/memory/state_bench.py --domain travel --num-runs 1 --out /tmp/state

For Cursor-only memory QA, use longmemeval / locomo / beam instead.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".cache" / "horizon-bench" / "STATE-Bench"
REPO = "https://github.com/microsoft/STATE-Bench.git"


def ensure_clone(root: Path) -> None:
    if (root / "pyproject.toml").is_file():
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", REPO, str(root)], check=True)


def write_skip(out: Path, reason: str) -> int:
    out.mkdir(parents=True, exist_ok=True)
    row = {"benchmark": "state_bench", "skipped": True, "reason": reason}
    (out / "status.json").write_text(json.dumps(row, indent=2) + "\n")
    print(f"STATE-Bench skipped: {reason}")
    return 0


def collect_metrics(results_dir: Path, num_runs: int) -> dict:
    passed = total = 0
    for run in range(1, num_runs + 1):
        run_dir = results_dir / f"run{run}"
        if not run_dir.is_dir():
            continue
        for p in run_dir.glob("*.json"):
            try:
                d = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            total += 1
            if d.get("success") or d.get("passed") or d.get("task_success"):
                passed += 1
            elif isinstance(d.get("metrics"), dict) and d["metrics"].get("success"):
                passed += 1
    return {"pass_at_1": passed, "tasks_scored": total}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="git clone STATE-Bench into STATE_BENCH_ROOT")
    ap.add_argument("--domain", default="travel", choices=["travel", "customer_support", "shopping_assistant"])
    ap.add_argument("--agent-model-name", default=os.environ.get("STATE_BENCH_AGENT_MODEL", "composer-2.5"))
    ap.add_argument("--num-runs", type=int, default=1)
    ap.add_argument("--num-workers", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-skip", action="store_true", help="exit 0 when credentials or uv are missing")
    args = ap.parse_args(argv)
    root = Path(os.environ.get("STATE_BENCH_ROOT", DEFAULT_ROOT))
    out = Path(args.out)

    if args.install:
        ensure_clone(root)
        print(f"STATE-Bench at {root}")
        return 0

    if os.environ.get("RUN_STATE_BENCH", "").strip() not in ("1", "true", "yes"):
        if args.allow_skip:
            return write_skip(out, "Set RUN_STATE_BENCH=1 to run (needs Azure eval client + agent credentials).")
        raise SystemExit("Refusing to run STATE-Bench without RUN_STATE_BENCH=1")

    ensure_clone(root)
    if not shutil_which("uv"):
        if args.allow_skip:
            return write_skip(out, "uv not installed (STATE-Bench requires uv).")
        raise SystemExit("uv not found; install https://docs.astral.sh/uv/")

    results = out / "results" / args.domain
    results.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "state_bench.scripts.run_batch",
        "--domain",
        args.domain,
        "--agent-model-name",
        args.agent_model_name,
        "--num-runs",
        str(args.num_runs),
        "--num-workers",
        str(args.num_workers),
        "--output-dir",
        str(results),
    ]
    env = os.environ.copy()
    proc = subprocess.run(cmd, cwd=root, env=env)
    if proc.returncode != 0:
        if args.allow_skip:
            return write_skip(out, f"run_batch exited {proc.returncode} (check STATE-Bench .env / eval client).")
        return proc.returncode
    metrics = collect_metrics(results, args.num_runs)
    summary = {"benchmark": "state_bench", "domain": args.domain, "num_runs": args.num_runs, **metrics}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


def shutil_which(cmd: str) -> str | None:
    from shutil import which

    return which(cmd)


if __name__ == "__main__":
    sys.exit(main())
