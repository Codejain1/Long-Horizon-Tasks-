"""Backlog benchmark: real multi-ticket work, scored as cost to working software.

    python bench/backlog.py run --scenario rental --arm runner --out DIR --model-url URL [--image IMG] [--fixups 2]
    python bench/backlog.py report --out DIR

One scenario (bench/scenarios/<name>: a starting repo, one ticket per session, hidden tests per ticket) is worked
through in order in ONE repo, as a team's backlog is. Early tickets set business rules that later tickets need
without repeating them. After each ticket the hidden tests of every ticket so far run outside the agent's view.
What still fails goes back as a QA report (each failing check and its first error line, never the tests or the
old tickets), for up to `--fixups` rounds: what forgetting costs is counted in rounds, minutes and tokens.

Arms, all on the same model and tools:
  plain   one tool loop, no plan/contract/watcher (runner --plain)
  runner  the long-horizon runner, no memory
  notes   the runner; every ticket and QA report is appended to NOTES.md in the repo, and the goal says to read it
  mem     the runner with its own fresh memrouter: each ticket and QA report goes in as the owner's message once
          its job is done (a job never retrieves its own goal), and every hidden-test result as a real outcome
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench as B  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent / "runner" / "longrun.py"
ARMS = ("plain", "runner", "notes", "mem")
NOTES_HINT = ("\n\nNOTES.md in the repo holds every ticket and QA report so far, including requirements earlier "
              "tickets set for later features: read it first and follow it.")


def scenario(name: str) -> tuple[Path, dict]:
    d = HERE / "scenarios" / name
    return d, json.loads((d / "scenario.json").read_text())


def hidden(sdir: Path, upto: int, repo: Path, work: Path, image: str | None) -> tuple[list[str], list[str], str]:
    """Run the hidden tests of tickets 1..upto against the repo: (passed, failed, output)."""
    h = work / "hidden"
    shutil.rmtree(h, ignore_errors=True)
    h.mkdir(parents=True)
    for f in sdir.glob("hidden/*.py"):
        if not f.name.startswith("test_s") or int(f.stem[6:]) <= upto:
            shutil.copy(f, h / f.name)
    pytest = "python -m pytest -q -rA -p no:cacheprovider --tb=line ."
    if image:
        cmd = ["podman", "run", "--rm", "-v", f"{repo.resolve()}:/w:Z", "-v", f"{h.resolve()}:/h:Z", "-w", "/h",
               "-e", "PYTHONPATH=/w", image, "bash", "-c", f"pip install -q pytest >/dev/null 2>&1; {pytest}"]
        env = None
    else:
        cmd, env = [sys.executable, *pytest.split()[1:]], {**os.environ, "PYTHONPATH": str(repo.resolve())}
    try:
        p = subprocess.run(cmd, cwd=h, capture_output=True, text=True, timeout=300, env=env)
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        out = "hidden tests timed out"
    passed = sorted(set(re.findall(r"^PASSED \S+::(\w+)", out, re.M)))
    failed = sorted(set(B.failed_tests(out)) - set(passed))
    return passed, failed, out


def qa_report(ticket: int, out: str) -> str:
    return (f"QA report after ticket #{ticket}: these acceptance checks fail. Fix the code so they pass, without "
            f"breaking anything else: {B.feedback(out)}. Run ./ci.sh before you finish.")


def run_job(goal: str, repo: Path, logs: Path, tag: str, args, port: int | None) -> dict:
    """One runner (or plain) job in the repo; returns minutes, tokens and the runner's own verdict."""
    cmd = [sys.executable, str(RUNNER), goal, "--model-url", args.model_url, "--workdir", str(repo),
           "--max-minutes", str(args.max_minutes), "--judge-db", str(Path(args.out) / "judge.db")]
    cmd += ["--plain"] if args.arm == "plain" else []
    cmd += ["--memory-url", f"http://127.0.0.1:{port}", "--scope", args.scenario] if port else []
    cmd += ["--local"] if args.local else []
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=args.max_minutes * 60 + 600)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
    log = repo / "run.jsonl"
    events = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    if log.exists():
        logs.mkdir(parents=True, exist_ok=True)
        shutil.move(str(log), logs / f"{tag}.jsonl")
    rep = next((e for e in reversed(events) if e.get("event") == "report"), {})
    u = rep.get("usage") or {}
    return {"minutes": round((time.time() - t0) / 60, 2), "ok": bool(rep.get("ok")), "warning_id": rep.get("warning_id"),
            "judge_id": rep.get("judge_id"),
            "tokens": u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)}


def _post(port: int | None, path: str, body: dict) -> None:
    if port:
        import urllib.request
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode()),
                               timeout=180).read()


def tell(port: int | None, text: str, rule: bool) -> None:
    _post(port, "/add", {"text": text, "speaker": "owner", "chat": "backlog", "detect_rule": rule})


def outcome(port: int | None, scope: str, passed: list[str], out: str, job: dict) -> None:
    """The real result of a job: its failing checks (with their messages) become experience for the scope."""
    if not port:
        return
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memrouter"))
    import failures as FL

    _post(port, "/outcome", {"scope": scope, "failed": FL.from_pytest(out), "passed": passed,
                             "warning_id": job.get("warning_id")})


def run(args, job=run_job) -> list[dict]:
    sdir, sc = scenario(args.scenario)
    work = Path(args.out) / args.arm
    repo, logs = work / "repo", work / "logs"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(sdir / "start", repo)
    mem, port = None, None
    if args.arm == "mem":
        port = args.port
        mem = B.start_memrouter(args.memrouter_cmd, work / "memrouter", port)
    rules = sc.get("rule_checks", {})
    rows = []
    try:
        for i, ticket in enumerate(sc["sessions"][:args.tickets], 1):
            goal = ticket
            if args.arm == "notes":
                with (repo / "NOTES.md").open("a") as f:
                    f.write(f"## Ticket #{i}\n{ticket}\n\n")
                goal += NOTES_HINT
            build = job(goal, repo, logs, f"t{i:02d}", args, port)
            tell(port, ticket, rule=True)
            passed, failed, out = hidden(sdir, i, repo, work, args.image)
            outcome(port, args.scenario, passed, out, build)
            B.label_judge(Path(args.out) / "judge.db", build.get("judge_id"), not failed)
            row = {"arm": args.arm, "scenario": args.scenario, "ticket": i, "build_min": build["minutes"],
                   "build_tokens": build["tokens"], "runner_ok": build["ok"], "after_build": len(passed),
                   "total": len(passed) + len(failed), "fixup_rounds": 0, "fixup_min": 0.0, "fixup_tokens": 0}
            for r in range(1, args.fixups + 1):
                if not failed:
                    break
                report = qa_report(i, out)
                if args.arm == "notes":
                    with (repo / "NOTES.md").open("a") as f:
                        f.write(f"## {report}\n\n")
                fix = job(report + (NOTES_HINT if args.arm == "notes" else ""), repo, logs, f"t{i:02d}-fix{r}", args, port)
                tell(port, report, rule=False)
                passed, failed, out = hidden(sdir, i, repo, work, args.image)
                outcome(port, args.scenario, passed, out, fix)
                B.label_judge(Path(args.out) / "judge.db", fix.get("judge_id"), not failed)
                row["fixup_rounds"], row["fixup_min"] = r, round(row["fixup_min"] + fix["minutes"], 2)
                row["fixup_tokens"] += fix["tokens"]
            row.update(passed=passed, failed=failed, final=len(passed),
                       rules_passed=sorted(t for t in passed if t in rules),
                       rules_present=sorted(t for t in passed + failed if t in rules))
            rows.append(row)
            with (Path(args.out) / f"{args.arm}.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
            print(json.dumps({k: row[k] for k in ("ticket", "after_build", "final", "total", "fixup_rounds")}), flush=True)
    finally:
        if mem:
            mem.terminate()
    return rows


def report(out: str) -> str:
    lines = []
    by = {}
    for f in sorted(Path(out).glob("*.jsonl")):
        rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
        if rows:
            by[rows[0]["arm"]] = rows
    for arm, rows in by.items():
        last = rows[-1]
        mins = sum(r["build_min"] + r["fixup_min"] for r in rows)
        toks = sum(r["build_tokens"] + r["fixup_tokens"] for r in rows)
        first_try = sum(r["after_build"] for r in rows) / max(sum(r["total"] for r in rows), 1)
        lines.append(f"{arm:7} tickets {len(rows):2}  hidden at the end {last['final']:2}/{last['total']:2}  "
                     f"rule checks {len(last['rules_passed'])}/{len(last['rules_present'])}  passing right after each "
                     f"ticket {100 * first_try:5.1f}%  fix-up rounds {sum(r['fixup_rounds'] for r in rows):2}  "
                     f"minutes {mins:6.1f}  tokens {toks / 1e6:5.2f}M  median build {statistics.median(r['build_min'] for r in rows):4.1f} min")
    if "runner" in by:  # paired on the final hidden tests: the same checks for every arm
        base = set(by["runner"][-1]["passed"])
        for arm, rows in by.items():
            if arm != "runner":
                mine = set(rows[-1]["passed"])
                lines.append(f"{arm} vs runner, final hidden tests: +{len(mine - base)} passed only here, "
                             f"-{len(base - mine)} passed only with the runner")
    return "\n".join(lines) or "no results yet"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--scenario", default="rental")
    r.add_argument("--arm", choices=ARMS, required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--model-url", required=True)
    r.add_argument("--tickets", type=int, default=99)
    r.add_argument("--fixups", type=int, default=2)
    r.add_argument("--max-minutes", type=float, default=15)
    r.add_argument("--image", default=None, help="score hidden tests in this container image")
    r.add_argument("--local", action="store_true", help="runner without a container (trusted use only)")
    r.add_argument("--port", type=int, default=8790)
    r.add_argument("--memrouter-cmd", default="")
    p = sub.add_parser("report")
    p.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "report":
        print(report(args.out))
        return 0
    run(args)
    print(report(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
