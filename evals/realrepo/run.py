"""A real project's history as an unattended backlog: one arm (baseline, lean or horizon), one session per commit.

    python evals/realrepo/run.py --arm lean --out /tmp/rr [--source /local/attrs/clone] [--tasks 8]

Each task is a real attrs change (`tasks.json`), written as the issue it solved. Before every session the
working copy is reset to that commit's parent, in the same directory, so Horizon's project memory carries over
as it would on a real project. The agent runs `./ci.sh`, standing in for attrs' GitHub CI: the test suite plus
the changelog-fragment check (`ci_checks/`). Afterwards the session is graded with the commit's real test files
over the agent's work (as SWE-bench does), the whole suite (regressions), and the changelog check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "longhorizon"))
from run import git, run_session, test_run_counts  # noqa: E402

TEST_DEPS = ["cloudpickle", "hypothesis", "pympler", "pytest>9", "pytest-xdist[psutil]"]
CI_SH = '#!/bin/sh\n# The project\'s CI: the test suite and the changelog check.\nexec python -m pytest -q -p no:cacheprovider tests ci_checks "$@"\n'
EXCLUDE = ["ci.sh", "ci_checks/", ".mcp.json", ".claude/", "CLAUDE.md", ".horizon/", "__pycache__/"]
WARNING = "failed in several earlier sessions"


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def setup(source: str, work: Path, arm: str) -> tuple[Path, dict]:
    repo = work / "repo"
    work.mkdir(parents=True)
    sh(["git", "clone", "-q", source, str(repo)])
    (repo / ".git" / "info" / "exclude").write_text("\n".join(EXCLUDE) + "\n")
    venv = work / "venv"
    sh(["uv", "venv", "-q", "-p", "3.13", str(venv)])
    sh(["uv", "pip", "install", "-q", "--python", str(venv / "bin" / "python"), *TEST_DEPS, "-e", str(repo)])
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "VIRTUAL_ENV")}
    env["PATH"] = f"{venv / 'bin'}:{env.get('PATH', '')}"  # `python` in the session and in ./ci.sh is the project's
    env["VIRTUAL_ENV"] = str(venv)
    if arm != "baseline":
        env["HORIZON_DB_URL"] = f"sqlite:///{work / 'horizon.db'}"
    return repo, env


def reset(repo: Path, commit: str, arm: str, env: dict) -> None:
    """The commit's parent, clean; the CI stand-in restored (the agent may have edited it); Horizon reinstalled
    (the reset undoes its edit of the tracked .gitignore)."""
    git(repo, "checkout", "-q", "-f", f"{commit}^")
    git(repo, "clean", "-q", "-fd")
    (repo / "ci.sh").write_text(CI_SH)
    (repo / "ci.sh").chmod(0o755)
    shutil.rmtree(repo / "ci_checks", ignore_errors=True)
    shutil.copytree(HERE / "ci_checks", repo / "ci_checks")
    if arm != "baseline":
        sh([sys.executable, "-m", "horizon", "install-claude-code", "--dir", str(repo),
            "--profile", "lean" if arm == "lean" else "full"], env=env)


def grade(repo: Path, commit: str, env: dict) -> dict:
    """The commit's real test files over the agent's work, the whole suite, and the changelog check."""
    shutil.rmtree(repo / "ci_checks", ignore_errors=True)
    shutil.copytree(HERE / "ci_checks", repo / "ci_checks")
    real_tests = [f for f in git(repo, "show", "--name-only", "--format=", commit).split()
                  if f.startswith("tests/") and f.endswith(".py")]
    if real_tests:
        git(repo, "checkout", commit, "--", *real_tests)
    proc = subprocess.run(["python", "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "tests", "ci_checks"],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=1200)
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", proc.stdout, re.M)
    passed_fragment = re.search(r"^PASSED ci_checks/\S+", proc.stdout, re.M) is not None
    test_failures = [f for f in failed if not f.startswith("ci_checks/")]
    summary = re.findall(r"^=*\s*(\d+ (?:passed|failed).*?) in [\d.]+s", proc.stdout, re.M)
    return {"resolved": not test_failures and bool(summary), "changelog_fragment": passed_fragment,
            "failed_tests": test_failures[:20], "summary": summary[-1] if summary else proc.stdout[-300:]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["baseline", "lean", "horizon"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--source", help="A local clone to copy (default: the repo in tasks.json).")
    ap.add_argument("--tasks", type=int, default=None, help="Stop after this many tasks.")
    ap.add_argument("--claude", default="claude")
    args = ap.parse_args(argv)

    spec = json.loads((HERE / "tasks.json").read_text())
    work = Path(args.out) / f"realrepo-{args.arm}"
    if work.exists():
        raise SystemExit(f"{work} exists; pick a fresh --out")
    repo, env = setup(args.source or spec["repo"], work, args.arm)
    rows = []
    for i, task in enumerate(spec["tasks"][: args.tasks], start=1):
        print(f"[realrepo/{args.arm}] task {i} ({task['commit']})", flush=True)
        reset(repo, task["commit"], args.arm, env)
        log = work / f"session{i}.jsonl"
        prompt = spec["preamble"] + "Task: " + task["issue"] + spec["suffix"]
        row = {"task": i, "commit": task["commit"],
               **run_session(prompt, repo, claude=args.claude, model=args.model, arm=args.arm, env=env, log=log)}
        events = [json.loads(line) for line in log.read_text().splitlines() if line.strip().startswith("{")]
        row["test_runs"], row["failed_test_runs"] = test_run_counts(events)
        row["warning_shown"] = WARNING in log.read_text()
        row.update(grade(repo, task["commit"], env))
        rows.append(row)
        (work / "results.json").write_text(json.dumps({"arm": args.arm, "model": args.model, "tasks": rows}, indent=2))
        print(f"   resolved {row['resolved']}, changelog {row['changelog_fragment']}, "
              f"failed runs {row['failed_test_runs']}/{row['test_runs']}, turns {row['turns']}, "
              f"cost {row['cost_usd']}, warning {row['warning_shown']}" + (f", ABORTED" if row["aborted"] else ""),
              flush=True)
        if row["aborted"]:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
