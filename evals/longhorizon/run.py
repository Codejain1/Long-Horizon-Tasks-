"""Long-horizon A/B: one scenario, one arm (baseline Claude Code, or Claude Code with Horizon), N sessions.

    python evals/longhorizon/run.py --scenario ledger --arm horizon --out /tmp/lh [--model sonnet]

Each session is a fresh `claude -p` with only that session's short prompt; the repo is committed between
sessions (a day boundary). After every session the hidden checks run outside the repo: the acceptance tests
of every session so far, and the constraints from session 1 (stdlib only, Python 3.9, unchanged public names).
Both arms get the same model, prompts, permissions and isolation (only the project's own settings, only the
MCP servers passed here); the only difference is whether Horizon is installed.

Arms: `baseline` (plain Claude Code), `observe` (plain Claude Code plus Horizon's silent event recorder, for
world-model data), `lean` (the default install) and `horizon` (the full workflow). Per session the harness also
records which checks failed in the agent's own test runs, repeat failures (a check failing again after an earlier
session saw it fail), regressions (a hidden test that passed after the previous session and doesn't now) and, for
arms with Horizon installed, the session's events and the context Horizon injected (`docs/EVALS.md`).

A session given as a list of prompts is one Claude Code session over several invocations, each resuming the last
(`compaction` sends "/compact" between them). Scenarios with `blocked_reasons` record whether each session stopped
with a `BLOCKED.md` note and whether it gave the right reason (`stall`); `--seed RUN_DIR` first replays another
run's sessions through the installed hooks, so Horizon has a history of normal work.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
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
PROFILES = {"horizon": "full", "lean": "lean", "observe": "observe", "candidate": "lean"}  # arm -> install profile
# The candidate arm is lean with the features still behind the evaluation gate turned on (docs/EVALS.md).
ARM_ENV = {"candidate": {"HORIZON_SESSION_MEMORY": "on", "HORIZON_CONTEXT": "composer", "HORIZON_STALL": "on"}}
EXTRA_ENV: dict[str, str] = {}  # --set KEY=VALUE, for ablations (e.g. the candidate without one feature)
MCP_ARMS = ("horizon", "lean", "candidate")  # arms whose agent gets Horizon's MCP tools
TEST_COMMAND = re.compile(r"pytest(?! --version)|ci\.sh")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout


def setup(scenario: dict, arm: str, work: Path, python: str, name: str = "repo",
          start_dir: Path | None = None, resume: tuple[Path, int] | None = None) -> tuple[Path, dict]:
    repo = work / name
    if resume:  # another run's repo as it was after session N-1
        source, first = resume
        work.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(source / "repo"), str(repo)], check=True)
        commit = git(repo, "log", "--format=%H", f"--grep=^after session {first - 1}$").split()[0]
        git(repo, "checkout", "-q", "-B", "main", commit)
    else:
        if scenario.get("start_from"):  # another scenario's codebase, with this scenario's files laid over it
            shutil.copytree(HERE / "scenarios" / scenario["start_from"] / "start", repo)
        if start_dir and start_dir.is_dir():  # a scenario that starts from an existing codebase
            shutil.copytree(start_dir, repo, dirs_exist_ok=True)
        repo.mkdir(parents=True, exist_ok=True)
        for name, text in scenario.get("start_files", {}).items():
            (repo / name).write_text(text)
            if name.endswith(".sh"):
                (repo / name).chmod(0o755)
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "start", "--allow-empty")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["PATH"] = f"{Path(python).parent}:{env.get('PATH', '')}"
    env.update(ARM_ENV.get(arm, {}))
    env.update(EXTRA_ENV)
    if arm in PROFILES:
        env["HORIZON_DB_URL"] = f"sqlite:///{work / 'horizon.db'}"
        subprocess.run([python, "-m", "horizon", "install-claude-code", "--dir", str(repo),
                        "--profile", PROFILES[arm]], check=True, env=env, capture_output=True)
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


def test_run_outputs(events: list[dict]) -> list[str]:
    ids = {b["id"] for b in blocks(events) if b.get("type") == "tool_use" and b.get("name") == "Bash"
           and TEST_COMMAND.search(str((b.get("input") or {}).get("command", "")))}
    return [text(b.get("content")) for b in blocks(events)
            if b.get("type") == "tool_result" and b.get("tool_use_id") in ids]


def test_run_counts(events: list[dict]) -> tuple[int, int]:
    """Test runs in a session, and how many failed: the cost of (re)discovering a project's rules by breaking them."""
    results = test_run_outputs(events)
    return len(results), sum(bool(re.search(r"\b\d+ (failed|errors?)\b", r)) for r in results)


def failing_checks(events: list[dict]) -> list[str]:
    """The checks that failed in the session's own test runs (pytest's FAILED/ERROR summary lines)."""
    return sorted({m for r in test_run_outputs(events)
                   for m in re.findall(r"^(?:FAILED|ERROR) (\S+::\S+)", r, re.M)})


def read_log(path: Path) -> list[dict]:
    lines = path.read_text().splitlines() if path.exists() else []
    return [e for e in map(_json_or_none, lines) if e is not None]


def horizon_view(work: Path, session_ids: list[str]) -> dict:
    """What Horizon's event log holds for one session (every id its invocations had): events by kind, and the
    characters it gave the agent."""
    db = work / "horizon.db"
    session_ids = [s for s in session_ids if s]
    if not session_ids or not db.exists():
        return {"events": {}, "injected_chars": 0, "stall_warnings": 0}
    with sqlite3.connect(db) as conn:
        try:
            rows = conn.execute(f"SELECT kind, data FROM events WHERE session_id IN ({','.join('?' * len(session_ids))})"
                                " ORDER BY n", session_ids).fetchall()
        except sqlite3.OperationalError:  # no hook has run yet, so no events table
            rows = []
    kinds: dict[str, int] = {}
    for kind, _ in rows:
        kinds[kind] = kinds.get(kind, 0) + 1
    datas = [json.loads(data).get("data") or {} for _, data in rows]
    return {"events": kinds, "injected_chars": sum(int(d.get("injected_chars") or 0) for d in datas),
            "stall_warnings": sum(bool(d.get("stall_warning")) for d in datas)}


def export_events(work: Path) -> int:
    """The run's whole event log as JSON Lines (world-model training data), in write order."""
    db = work / "horizon.db"
    if not db.exists():
        return 0
    with sqlite3.connect(db) as conn:
        try:
            rows = conn.execute("SELECT data FROM events ORDER BY n").fetchall()
        except sqlite3.OperationalError:
            rows = []
    (work / "events.jsonl").write_text("".join(json.dumps(json.loads(r[0])) + "\n" for r in rows))
    return len(rows)


def seed_test_runs(source: Path, sessions: range, repo: Path, env: dict, python: str) -> int:
    """Replay another run's real test runs through Horizon's own PostToolUse hook, as if they had happened in
    this project under Horizon: its memory starts with that history (e.g. a check failing in 2 sessions)."""
    n = 0
    for i in sessions:
        events = [json.loads(line) for line in (source / f"session{i}.jsonl").read_text().splitlines() if line.strip()]
        commands = {b["id"]: (b.get("input") or {}).get("command", "") for b in blocks(events)
                    if b.get("type") == "tool_use" and b.get("name") == "Bash"}
        for b in blocks(events):
            if b.get("type") == "tool_result" and b.get("tool_use_id") in commands:
                payload = {"session_id": f"seed-{i}", "cwd": str(repo), "tool_name": "Bash",
                           "tool_input": {"command": commands[b["tool_use_id"]]},
                           "tool_response": {"stdout": text(b.get("content")), "stderr": ""}}
                subprocess.run([python, "-m", "horizon", "hook", "post-tool-use", "--profile", "lean"],
                               input=json.dumps(payload), text=True, env=env, check=True, capture_output=True)
                n += 1
    return n


def aborted(result: dict) -> str | None:
    if not result:
        return "no result"
    return "usage limit" if any(t in str(result.get("result") or "").lower() for t in LIMIT_TEXT) else None


def seed_history(source: Path, repo: Path, env: dict, python: str, profile: str) -> int:
    """Replay another run's sessions through this arm's installed hooks, as Claude Code would have fired them
    (start, prompt, every tool call with its result, stop, end), so Horizon's event log starts with a history of
    normal work to forecast from. Returns the number of hook calls."""
    meta = json.loads((source / "results.json").read_text())
    prompts = json.loads((HERE / "scenarios" / meta["scenario"] / "scenario.json").read_text())["sessions"]
    calls = 0

    def fire(name: str, session: str, **payload) -> None:
        nonlocal calls
        body = {"session_id": session, "cwd": str(repo), **payload}
        subprocess.run([python, "-m", "horizon", "hook", name, "--profile", profile], input=json.dumps(body),
                       text=True, env=env, check=True, capture_output=True)
        calls += 1

    for row in meta["sessions"]:
        i, session = row["session"], f"seed-{meta['scenario']}-{row['session']}"
        events = read_log(source / f"session{i}.jsonl")
        prompt = prompts[i - 1] if isinstance(prompts[i - 1], str) else prompts[i - 1][0]
        uses = {b["id"]: b for b in blocks(events) if b.get("type") == "tool_use"}
        fire("session-start", session, source="startup")
        fire("user-prompt", session, prompt=prompt)
        for b in blocks(events):
            if b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                use = uses[b["tool_use_id"]]
                fire("post-tool-use", session, tool_name=use.get("name", ""), tool_input=use.get("input") or {},
                     tool_response={"stdout": text(b.get("content")), "stderr": ""})
        fire("stop", session, stop_hook_active=False)
        fire("session-end", session, reason="other")
    return calls


def blocked_note(repo: Path, reason: str | None) -> dict:
    """Whether the session stopped with a note (`BLOCKED.md` written in it) and, on a ticket that can't be done,
    whether the note gives the right reason (the scenario's pattern)."""
    written = "BLOCKED.md" in git(repo, "diff", "--name-only", "HEAD~1", "HEAD").split() \
        and (repo / "BLOCKED.md").exists()
    note = (repo / "BLOCKED.md").read_text()[:2000] if written else ""
    return {"written": written, "right_reason": bool(written and re.search(reason, note, re.I)) if reason else None,
            "note": note[:300]}


def run_session(prompts: str | list[str], repo: Path, *, claude: str, model: str, arm: str, env: dict,
                log: Path) -> dict:
    """One Claude Code session. A list of prompts is one session driven by several invocations: each after the
    first resumes the previous one's session (`--resume`), so e.g. "/compact" compacts the context mid-job."""
    prompts = [prompts] if isinstance(prompts, str) else list(prompts)
    start = time.monotonic()
    streams, results, invocations, session_id = [], [], [], None
    for prompt in prompts:
        cmd = [claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
               "--model", model, "--setting-sources", "project,local", "--strict-mcp-config", "--allowedTools",
               *AGENT_TOOLS, *(HORIZON_TOOLS if arm in MCP_ARMS else [])]
        if session_id:
            cmd += ["--resume", session_id]
        if arm in MCP_ARMS:
            cmd += ["--mcp-config", ".mcp.json"]
        if arm in PROFILES:
            cmd += ["--include-hook-events"]
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=45 * 60,
                              stdin=subprocess.DEVNULL)
        streams.append(proc.stdout if proc.stdout.endswith("\n") or not proc.stdout else proc.stdout + "\n")
        events = [e for e in map(_json_or_none, proc.stdout.splitlines()) if e is not None]
        result = next((e for e in reversed(events) if e.get("type") == "result"), {}) or {}
        results.append(result)
        session_id = result.get("session_id") or session_id
        invocations.append({"prompt": prompt[:60], "session_id": result.get("session_id"),
                            "turns": int(result.get("num_turns") or 0),
                            "cost_usd": round(float(result.get("total_cost_usd") or 0), 4)})
        if aborted(result):
            break
    log.write_text("".join(streams))
    events = read_log(log)
    tools = [b.get("name", "") for b in blocks(events) if b.get("type") == "tool_use"]
    test_runs, failed_runs = test_run_counts(events)
    row = {
        "aborted": next((a for a in map(aborted, results) if a), None),
        "session_id": session_id,
        "turns": sum(int(r.get("num_turns") or 0) for r in results),
        "tool_calls": len(tools),
        "test_runs": test_runs,
        "failed_test_runs": failed_runs,
        "failed_checks": failing_checks(events),
        "horizon_calls": {t.removeprefix("mcp__horizon__"): tools.count(t) for t in sorted(set(tools))
                          if t.startswith("mcp__horizon__")},
        "cost_usd": round(sum(float(r.get("total_cost_usd") or 0) for r in results), 4),
        "tokens": sum(int((r.get("usage") or {}).get(k) or 0) for r in results
                      for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "wall_s": round(time.monotonic() - start, 1),
    }
    if len(prompts) > 1:
        row["invocations"] = invocations
        row["compactions"] = sum(e.get("type") == "system" and "compact" in str(e.get("subtype", "")) for e in events)
    return row


def _json_or_none(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


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
    """Session 1's constraints, checked in a separate process (a fresh import of the agent's package):
    stdlib only, Python 3.9 syntax, integer cents (no floats), uuid4().hex ids, UTC only and unchanged public names."""
    pkg = repo / package
    if not pkg.is_dir():
        return [f"package {package}/ missing"]
    code = (f"import json, sys; sys.path.insert(0, {str(HERE)!r}); import checks; from pathlib import Path; "
            f"c = json.loads({json.dumps(json.dumps(scenario['constraints']))}); p = Path({str(pkg)!r}); v = []\n"
            "if c.get('stdlib_only'): v += ['stdlib: ' + x for x in checks.stdlib_only(p)]\n"
            "if c.get('python39'): v += ['py39: ' + x for x in checks.python39(p)]\n"
            "if c.get('no_floats'): v += ['cents: ' + x for x in checks.no_floats(p)]\n"
            "if c.get('uuid_hex'): v += ['ids: ' + x for x in checks.uuid_hex_ids(p)]\n"
            "if c.get('utc_only'): v += ['utc: ' + x for x in checks.utc_only(p)]\n"
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
    ap.add_argument("--arm", choices=["baseline", "observe", "lean", "candidate", "horizon"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--sessions", type=int, default=None, help="Stop after this many sessions.")
    ap.add_argument("--claude", default="claude", help="The claude CLI (tests pass a fake).")
    ap.add_argument("--resume", metavar="RUN_DIR", help="Start from another run's repo (e.g. OUT/backlog-baseline) "
                                                        "after session --from-session minus 1; Horizon arms are seeded "
                                                        "with that run's test history.")
    ap.add_argument("--from-session", type=int, default=1)
    ap.add_argument("--seed", metavar="RUN_DIR", help="Before session 1, replay that run's sessions through the "
                                                      "installed hooks (arms with Horizon), e.g. for `stall`.")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="An extra environment setting for the arm, e.g. HORIZON_CONTEXT=classic (repeatable).")
    args = ap.parse_args(argv)
    EXTRA_ENV.clear()
    EXTRA_ENV.update(kv.split("=", 1) for kv in args.set)

    scenario_dir = HERE / "scenarios" / args.scenario
    scenario = json.loads((scenario_dir / "scenario.json").read_text())
    work = Path(args.out) / f"{args.scenario}-{args.arm}"
    if work.exists():
        raise SystemExit(f"{work} exists; pick a fresh --out")
    python = sys.executable
    # "repos": one fresh repo per session (a new project for the same team), sharing only Horizon's memory.
    # Then each session's hidden tests run on its own repo; otherwise they accumulate on the one repo.
    repos = scenario.get("repos")
    resume = (Path(args.resume), args.from_session) if args.resume else None
    if not repos:
        repo, env = setup(scenario, args.arm, work, python, start_dir=scenario_dir / "start", resume=resume)
    if resume and args.arm in MCP_ARMS:
        seeded = seed_test_runs(resume[0], range(1, args.from_session), repo, env, python)
        print(f"[{args.scenario}/{args.arm}] seeded {seeded} test runs from {resume[0]}", flush=True)
    if args.seed and args.arm in PROFILES and not repos:
        calls = seed_history(Path(args.seed), repo, env, python, PROFILES[args.arm])
        print(f"[{args.scenario}/{args.arm}] replayed {calls} hook calls from {args.seed}", flush=True)
    # Checks that already failed before this run's first session: every arm of a replay starts with the same history.
    seen = {c for i in range(1, args.from_session) if resume
            for c in failing_checks(read_log(resume[0] / f"session{i}.jsonl"))}
    source_rows = json.loads((resume[0] / "results.json").read_text())["sessions"] \
        if resume and (resume[0] / "results.json").exists() else []
    passed_before = {t for r in source_rows if r["session"] == args.from_session - 1 for t in r["hidden"]["passed"]}
    rows = []
    for i, prompt in enumerate(scenario["sessions"][: args.sessions], start=1):
        if i < args.from_session:
            continue
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
        if "blocked_reasons" in scenario:
            row["blocked"] = blocked_note(repo, scenario["blocked_reasons"].get(str(i)))
        row["repeat_failures"] = sorted(seen & set(row["failed_checks"]))
        seen |= set(row["failed_checks"])
        passed = set(row["hidden"]["passed"])
        row["regressions"] = [] if repos else sorted(passed_before - passed)  # separate repos can't regress
        passed_before = passed
        if args.arm in PROFILES:
            row["horizon"] = horizon_view(work, sorted({i["session_id"] for i in row.get("invocations", [])}
                                                       | {row["session_id"]}))
        rows.append(row)
        (work / "results.json").write_text(json.dumps({"scenario": args.scenario, "arm": args.arm,
                                                       "model": args.model,
                                                       "settings": {**ARM_ENV.get(args.arm, {}), **EXTRA_ENV},
                                                       "sessions": rows}, indent=2))
        print(f"   hidden {len(row['hidden']['passed'])} passed / {len(row['hidden']['failed'])} failed, "
              f"violations {len(row['violations'])}, turns {row['turns']}, tools {row['tool_calls']}"
              + (f", ABORTED ({row['aborted']})" if row["aborted"] else ""), flush=True)
        if row["aborted"]:
            break
    if args.arm in PROFILES:
        print(f"[{args.scenario}/{args.arm}] exported {export_events(work)} events", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
