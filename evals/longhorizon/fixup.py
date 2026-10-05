"""Cost to correct software: continue a finished run with fix-up sessions until its hidden tests pass.

A run without Horizon ends cheaper partly because it skipped work (the rules it forgot); in a real project the
failures surface later and get fixed then. This continues a finished run in place (its repo, and for lean its
Horizon memory), giving the agent each round the failing checks and their error lines, as a CI or QA report
would. It stops when everything passes or after `--rounds`. Both arms get the same report format.

    python evals/longhorizon/fixup.py --run /tmp/rep-ward2/with/ward-lean --scenario ward --arm lean --out /tmp/fx
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
from pathlib import Path

import run as R

PROMPT = ("QA found problems in the software you're building in this repo: the checks below fail. Fix them "
          "without breaking anything else, and run ./ci.sh before you finish.\n{failures}")
# A first try gave only the failing checks, and no agent could fix a forgotten rule: the repo doesn't record the
# rules, so the agent asked what they were. A person would paste the requirement tickets back in; so does this.
SOURCES = "\n\nThe requirements behind them, from earlier tickets:\n{tickets}"
# A developer fixing a QA report can look the requirements up again. Without them, an agent that never got a
# rule (it was in an earlier ticket) won't guess thresholds and formats, and fixes nothing (ward, 3 rounds).
BACKLOG = "\n\nThe backlog these features came from, as the tickets were written:\n{tickets}"


def failures(scenario_dir: Path, sessions: int, repo: Path, python: str) -> list[str]:
    """The failing hidden checks as CI shows them: `test_name: first error line`."""
    import xml.etree.ElementTree as ET

    with tempfile.TemporaryDirectory() as tmp:
        tests = Path(tmp, "t")
        tests.mkdir()
        for i in range(1, sessions + 1):
            shutil.copy(scenario_dir / "hidden" / f"test_s{i}.py", tests)
        for helper in (scenario_dir / "hidden").glob("[!t]*.py"):
            shutil.copy(helper, tests)
        report = Path(tmp, "report.xml")
        env = {k: v for k, v in os.environ.items() if k not in ("FORCE_COLOR", "PY_COLORS")}
        subprocess.run([python, "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider", f"--junitxml={report}",
                        str(tests)], env={**env, "PYTHONPATH": str(repo), "NO_COLOR": "1"},
                       capture_output=True, text=True, timeout=900)
        out = []
        for case in ET.parse(report).iter("testcase"):
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            if bad is not None:
                msg = " / ".join((bad.get("message") or "failed").strip().splitlines()[:3])
                ticket = re.search(r"test_s(\d+)", case.get("classname") or "")
                out.append(f"{case.get('name')}" + (f" (ticket #{ticket.group(1)})" if ticket else "")
                           + f": {msg[:400]}")
        return out


def backlog(scenario_dir: Path, sessions: int) -> str:
    """Every ticket's text without the preamble they all share."""
    from horizon.composer import label

    tickets = json.loads((scenario_dir / "scenario.json").read_text())["sessions"][:sessions]
    return BACKLOG.format(tickets="\n".join(f"- {label(t, tickets, most=10_000)}" for t in tickets))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="A finished run's folder, e.g. OUT/with/ward-lean")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--arm", choices=["baseline", "lean"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--agent", choices=["claude", "cursor"], default="claude")
    ap.add_argument("--no-backlog", action="store_true", help="Report the failing checks only.")
    ap.add_argument("--deny-read", action="append", default=[])
    args = ap.parse_args(argv)
    R.AGENT.update(host=args.agent, bin="agent" if args.agent == "cursor" else "claude")
    if args.agent == "cursor":
        R.SESSION_TIMEOUT = min(R.SESSION_TIMEOUT, 25 * 60)

    source = Path(args.run).resolve()
    scenario_dir = R.HERE / "scenarios" / args.scenario
    spec = json.loads((scenario_dir / "scenario.json").read_text())
    sessions = len(spec["sessions"])
    repo = (source / "repo").resolve()  # in place: lean's memory is keyed by this path
    work = Path(args.out).resolve() / f"{args.scenario}-{args.arm}"
    work.mkdir(parents=True)
    R.ISOLATE["on"] = True
    R.RUN["repo"] = repo
    R.DENY_READ[:] = [R.REPO_ROOT, Path(args.out).resolve(), source,
                      *(Path(p).expanduser().resolve() for p in args.deny_read)]
    python = R.agent_python()
    env = R.agent_env(python)
    if args.arm == "lean":
        db = Path(tempfile.mkdtemp(prefix=R.TEMP_PREFIX)).resolve() / "horizon.db"
        shutil.copy(source / "horizon.db", db)
        R.RUN["db"] = db
        env["HORIZON_DB_URL"] = f"sqlite:///{db}"
        env.setdefault("HORIZON_SCORER", "jev")
        # the run's hooks name the interpreter it was built with, which a newer Horizon may have replaced
        install = ["install-cursor", "--no-mcp"] if args.agent == "cursor" else ["install-claude-code"]
        subprocess.run([python, "-m", "horizon", *install, "--dir", str(repo), "--profile", "lean"], env=env,
                       check=True, capture_output=True)
        R.git(repo, "add", "-A")
        R.git(repo, "commit", "-qm", "hooks for fix-ups", "--allow-empty")
    before = json.loads((source / "results.json").read_text())["sessions"]
    rows = []
    failing = failures(scenario_dir, sessions, repo, sys.executable)
    print(f"[{args.scenario}/{args.arm}] {len(failing)} failing before fix-ups", flush=True)
    for i in range(1, args.rounds + 1):
        if not failing:
            break
        prompt = PROMPT.format(failures="\n".join(f"- {f}" for f in failing))
        cited = sorted({n for f in failing for n in spec.get("rule_sources", {}).get(f.split(":")[0], [])})
        if cited:
            prompt += SOURCES.format(tickets="\n\n".join(spec["sessions"][n - 1] for n in cited)) \
            + ("" if args.no_backlog else backlog(scenario_dir, sessions))
        row = {"round": i, "failing_before": len(failing),
               **R.run_session(prompt, repo, claude="claude", model=args.model, arm=args.arm, env=env,
                               log=work / f"fixup{i}.jsonl")}
        R.git(repo, "add", "-A")
        R.git(repo, "commit", "-qm", f"after fix-up {i}", "--allow-empty")
        failing = failures(scenario_dir, sessions, repo, sys.executable)
        row["failing_after"] = len(failing)
        rows.append(row)
        print(f"   round {i}: {row['failing_before']} -> {row['failing_after']} failing, {row['turns']} turns, "
              f"{row['wall_s']:.0f} s, ${row['cost_usd']:.2f}" + (" ABORTED" if row["aborted"] else ""), flush=True)
        if row["aborted"]:
            break
    if R.RUN.get("db"):
        shutil.copy(R.RUN["db"], work / "horizon.db")
    built = {k: sum(r.get(k, 0) for r in before) for k in ("cost_usd", "tokens", "wall_s")}
    fix = {k: sum(r.get(k, 0) for r in rows) for k in ("cost_usd", "tokens", "wall_s")}
    summary = {"scenario": args.scenario, "arm": args.arm, "source": str(source), "rounds": rows,
               "failing_at_end": failing, "build": built, "fixups": fix,
               "total": {k: built[k] + fix[k] for k in built}}
    (work / "results.json").write_text(json.dumps(summary, indent=2))
    print(f"   total to correct: ${summary['total']['cost_usd']:.2f}, {summary['total']['wall_s'] / 60:.1f} min, "
          f"{len(failing)} still failing", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
