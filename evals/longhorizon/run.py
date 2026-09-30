"""Long-horizon A/B: one scenario, one arm (baseline Claude Code, or Claude Code with Horizon), N sessions.

    python evals/longhorizon/run.py --scenario ledger --arm horizon --out /tmp/lh [--model sonnet]

Each session is a fresh `claude -p` with only that session's short prompt; the repo is committed between
sessions (a day boundary). After every session the hidden checks run outside the repo: the acceptance tests
of every session so far, and the constraints from session 1 (stdlib only, Python 3.9, unchanged public names).
Both arms get the same model, prompts, permissions and isolation (only the project's own settings, only the
MCP servers passed here); the only difference is whether Horizon is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
AGENT_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash(python:*)", "Bash(python3:*)", "Bash(pytest:*)",
               "Bash(ls:*)", "Bash(mkdir:*)", "Bash(cat:*)", "Bash(git status:*)", "Bash(git diff:*)",
               "Bash(git log:*)", "Bash(git -C:*)", "Bash(git restore:*)", "Bash(rm -rf .horizon/spikes:*)",
               "Bash(./ci.sh:*)", "Bash(sh ci.sh:*)"]
HORIZON_TOOLS = ["mcp__horizon__start_task", "mcp__horizon__recall_context", "mcp__horizon__evaluate_options",
                 "mcp__horizon__submit_consequences", "mcp__horizon__record_outcome", "mcp__horizon__explain_decision",
                 "mcp__horizon__show_memories"]
LIMIT_TEXT = ("session limit", "usage limit", "rate limit")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout


def setup(scenario: dict, arm: str, work: Path, python: str, name: str = "repo",
          start_dir: Path | None = None) -> tuple[Path, dict]:
    repo = work / name
    if start_dir and start_dir.is_dir():  # a scenario that starts from an existing codebase
        shutil.copytree(start_dir, repo)
    repo.mkdir(parents=True, exist_ok=True)
    for name, text in scenario.get("start_files", {}).items():
        (repo / name).write_text(text)
        if name.endswith(".sh"):
            (repo / name).chmod(0o755)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "start")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["PATH"] = f"{Path(python).parent}:{env.get('PATH', '')}"
    if arm in ("horizon", "lean"):
        env["HORIZON_DB_URL"] = f"sqlite:///{work / 'horizon.db'}"
        subprocess.run([python, "-m", "horizon", "install-claude-code", "--dir", str(repo),
                        "--profile", "lean" if arm == "lean" else "full"], check=True, env=env,
                       capture_output=True)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "install horizon")
    return repo, env


def blocks(events: list[dict]) -> list[dict]:
    return [b for e in events if e.get("type") in ("assistant", "user") and isinstance(e.get("message"), dict)
            for b in e["message"].get("content") or [] if isinstance(b, dict)]


def text(content) -> str:
    """A tool result's text, newlines intact (JSON-escaping them broke word boundaries: "\\n1 failed")."""
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "")


def test_run_counts(events: list[dict]) -> tuple[int, int]:
    """Test runs in a session, and how many failed: the cost of (re)discovering a project's rules by breaking them."""
    ids = {b["id"] for b in blocks(events) if b.get("type") == "tool_use" and b.get("name") == "Bash"
           and re.search(r"pytest(?! --version)|ci\.sh", str((b.get("input") or {}).get("command", "")))}
    results = [text(b.get("content")) for b in blocks(events)
               if b.get("type") == "tool_result" and b.get("tool_use_id") in ids]
    return len(results), sum(bool(re.search(r"\b\d+ (failed|errors?)\b", r)) for r in results)


def run_session(prompt: str, repo: Path, *, claude: str, model: str, arm: str, env: dict, log: Path) -> dict:
    cmd = [claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
           "--model", model, "--setting-sources", "project,local", "--strict-mcp-config", "--allowedTools",
           *AGENT_TOOLS, *(HORIZON_TOOLS if arm != "baseline" else [])]
    if arm != "baseline":
        cmd += ["--mcp-config", ".mcp.json", "--include-hook-events"]
    start = time.monotonic()
    proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=45 * 60,
                          stdin=subprocess.DEVNULL)
    log.write_text(proc.stdout)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    result = next((e for e in reversed(events) if e.get("type") == "result"), {}) or {}
    tools = [b.get("name", "") for b in blocks(events) if b.get("type") == "tool_use"]
    test_runs, failed_runs = test_run_counts(events)
    usage = result.get("usage") or {}
    text = str(result.get("result") or "").lower()
    return {
        "aborted": "usage limit" if any(t in text for t in LIMIT_TEXT) else (None if result else "no result"),
        "turns": int(result.get("num_turns") or 0),
        "tool_calls": len(tools),
        "test_runs": test_runs,
        "failed_test_runs": failed_runs,
        "horizon_calls": {t.removeprefix("mcp__horizon__"): tools.count(t) for t in sorted(set(tools))
                          if t.startswith("mcp__horizon__")},
        "cost_usd": round(float(result.get("total_cost_usd") or 0), 4),
        "tokens": sum(int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens")),
        "wall_s": round(time.monotonic() - start, 1),
    }


def hidden_tests(scenario_dir: Path, sessions: range, repo: Path, python: str) -> dict:
    """Acceptance tests of the given sessions, run outside the repo against the agent's package."""
    with tempfile.TemporaryDirectory() as tmp:
        for i in sessions:
            shutil.copy(scenario_dir / "hidden" / f"test_s{i}.py", tmp)
        for helper in (scenario_dir / "hidden").glob("[!t]*.py"):  # shared checks, e.g. team conventions
            shutil.copy(helper, tmp)
        for always in (scenario_dir / "hidden").glob("test_always_*.py"):  # checked after every session
            shutil.copy(always, tmp)
        proc = subprocess.run([python, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "--tb=no", tmp],
                              env={**os.environ, "PYTHONPATH": str(repo)}, capture_output=True, text=True, timeout=600)
    passed = re.findall(r"^PASSED \S+::(\S+)", proc.stdout, re.M)
    failed = re.findall(r"^(?:FAILED|ERROR) \S+?(?:::(\S+))?(?: |$)", proc.stdout, re.M)
    return {"passed": sorted(passed), "failed": sorted(f or "collection" for f in failed)}


def constraints(scenario: dict, repo: Path, python: str, package: str) -> list[str]:
    """Session 1's constraints, checked in a separate process (a fresh import of the agent's package)."""
    pkg = repo / package
    if not pkg.is_dir():
        return [f"package {package}/ missing"]
    code = (f"import json, sys; sys.path.insert(0, {str(HERE)!r}); import checks; from pathlib import Path; "
            f"c = json.loads({json.dumps(json.dumps(scenario['constraints']))}); p = Path({str(pkg)!r}); v = []\n"
            "if c.get('stdlib_only'): v += ['stdlib: ' + x for x in checks.stdlib_only(p)]\n"
            "if c.get('python39'): v += ['py39: ' + x for x in checks.python39(p)]\n"
            "v += ['names: ' + x for x in checks.public_names(p, c.get('public_names', {}))]\n"
            "print(json.dumps(v))")
    proc = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"checks crashed: {proc.stderr[-300:]}"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--arm", choices=["baseline", "horizon", "lean"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--sessions", type=int, default=None, help="Stop after this many sessions.")
    ap.add_argument("--claude", default="claude", help="The claude CLI (tests pass a fake).")
    args = ap.parse_args(argv)

    scenario_dir = HERE / "scenarios" / args.scenario
    scenario = json.loads((scenario_dir / "scenario.json").read_text())
    work = Path(args.out) / f"{args.scenario}-{args.arm}"
    if work.exists():
        raise SystemExit(f"{work} exists; pick a fresh --out")
    python = sys.executable
    # "repos": one fresh repo per session (a new project for the same team), sharing only Horizon's memory.
    # Then each session's hidden tests run on its own repo; otherwise they accumulate on the one repo.
    repos = scenario.get("repos")
    if not repos:
        repo, env = setup(scenario, args.arm, work, python, start_dir=scenario_dir / "start")
    rows = []
    for i, prompt in enumerate(scenario["sessions"][: args.sessions], start=1):
        print(f"[{args.scenario}/{args.arm}] session {i}", flush=True)
        if repos:
            repo, env = setup(scenario, args.arm, work, python, repos[i - 1])
        package = repos[i - 1] if repos else scenario["package"]
        row = {"session": i, **run_session(prompt, repo, claude=args.claude, model=args.model, arm=args.arm, env=env,
                                           log=work / f"session{i}.jsonl")}
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", f"after session {i}", "--allow-empty")
        row["hidden"] = hidden_tests(scenario_dir, range(i, i + 1) if repos else range(1, i + 1), repo, python)
        row["violations"] = constraints(scenario, repo, python, package)
        rows.append(row)
        (work / "results.json").write_text(json.dumps({"scenario": args.scenario, "arm": args.arm,
                                                       "model": args.model, "sessions": rows}, indent=2))
        print(f"   hidden {len(row['hidden']['passed'])} passed / {len(row['hidden']['failed'])} failed, "
              f"violations {len(row['violations'])}, turns {row['turns']}, tools {row['tool_calls']}"
              + (f", ABORTED ({row['aborted']})" if row["aborted"] else ""), flush=True)
        if row["aborted"]:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
