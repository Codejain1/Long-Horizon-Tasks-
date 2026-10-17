"""DolphinBench wrapper (mem0ai/dolphinbench) — tool-using agent tasks, not recall QA.

Official runs need Azure grading credentials and a harness implementing run_agent / freeze / verify_checkpoint.
This script installs the runner repo and optionally starts ingest+evaluate when explicitly enabled.

    export DOLPHINBENCH_ROOT=~/.cache/horizon-bench/dolphinbench
    export RUN_DOLPHINBENCH=1
    export AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=...
    python evals/memory/dolphinbench.py --install
    python evals/memory/dolphinbench.py --out /tmp/dolphin

See https://dolphinbench.ai/run/ and evals/memory/adapters/dolphinbench_harness_stub.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".cache" / "horizon-bench" / "dolphinbench"
REPO = "https://github.com/mem0ai/dolphinbench.git"
STUB = Path(__file__).resolve().parent / "adapters" / "dolphinbench_harness_stub.py"


def ensure_clone(root: Path) -> None:
    if (root / "harness" / "runner.py").is_file():
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", REPO, str(root)], check=True)


def write_skip(out: Path, reason: str) -> int:
    out.mkdir(parents=True, exist_ok=True)
    (out / "status.json").write_text(json.dumps({"benchmark": "dolphinbench", "skipped": True, "reason": reason}, indent=2) + "\n")
    print(f"DolphinBench skipped: {reason}")
    return 0


def count_hf_tests(max_scan: int = 5) -> int:
    """Quick HF connectivity check; release size is fixed at 600 tests."""
    from datasets import load_dataset

    ds = load_dataset("mem0ai/dolphinbench", "tests", split="test", streaming=True)
    n = 0
    for _ in ds:
        n += 1
        if n >= max_scan:
            break
    return 600 if n >= 1 else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="verify HF tests dataset and clone only")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-skip", action="store_true")
    args = ap.parse_args(argv)
    root = Path(os.environ.get("DOLPHINBENCH_ROOT", DEFAULT_ROOT))
    out = Path(args.out)

    if args.install or args.smoke:
        ensure_clone(root)
        n = count_hf_tests()
        print(f"DolphinBench: repo at {root}, HF tests rows seen: {n}")
        if args.smoke:
            out.mkdir(parents=True, exist_ok=True)
            (out / "smoke.json").write_text(json.dumps({"tests_in_release": n}, indent=2) + "\n")
        return 0

    if os.environ.get("RUN_DOLPHINBENCH", "").strip() not in ("1", "true", "yes"):
        if args.allow_skip:
            return write_skip(out, "Set RUN_DOLPHINBENCH=1 and implement harness (see adapters/dolphinbench_harness_stub.py).")
        raise SystemExit("Refusing to run DolphinBench without RUN_DOLPHINBENCH=1")

    if not os.environ.get("AZURE_OPENAI_API_KEY") or not os.environ.get("AZURE_OPENAI_ENDPOINT"):
        if args.allow_skip:
            return write_skip(out, "DolphinBench grading requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY.")
        raise SystemExit("Missing Azure OpenAI grading credentials")

    ensure_clone(root)
    if not STUB.is_file():
        return write_skip(out, f"Missing harness stub at {STUB}")

    # User copies stub to my_harness.py per dolphinbench init flow.
    harness = root / "my_harness.py"
    if not harness.is_file():
        harness.write_text(STUB.read_text())
        print(f"Wrote starter harness to {harness} — wire Horizon memory before evaluate.")

    py = sys.executable
    for stage, extra in (
        ("prepare", []),
        ("ingest", ["--confirm-paid-calls"]),
        ("evaluate", ["--confirm-paid-calls"]),
    ):
        cmd = [py, "-m", "harness.runner", stage, *extra]
        proc = subprocess.run(cmd, cwd=root)
        if proc.returncode != 0:
            if args.allow_skip:
                return write_skip(out, f"harness.runner {stage} exited {proc.returncode}")
            return proc.returncode
    (out / "status.json").write_text(json.dumps({"benchmark": "dolphinbench", "completed": True}, indent=2) + "\n")
    print("DolphinBench run finished; package with: python -m harness.runner package")
    return 0


if __name__ == "__main__":
    sys.exit(main())
