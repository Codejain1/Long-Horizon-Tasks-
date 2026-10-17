"""Fast long-horizon benchmark: does experience make later jobs better? Objective, parallel, seeded.

    python bench.py run --out DIR --streams mem:1,mem:2,none:1,none:2 --n 16 --model-url URL [--image IMG] [--set house]
    python bench.py report --out DIR

Each stream does `n` tasks from `tasks.py` in its own shuffled order, one after another; streams run in parallel.
Streams with the same seed get the same order, so "mem:1" vs "none:1" is a paired comparison. A "mem" stream gets
its own fresh memrouter (memory builds up over its tasks); "rules" is the same but the runner takes only the owner's
standing rules from it; "none" runs the same runner without memory. Every job is
scored by the task's HIDDEN acceptance tests (never shown to the agent), inside the sandbox image when given.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tasks as T  # noqa: E402

RUNNER = Path(__file__).resolve().parents[1] / "runner" / "longrun.py"


def order(seed: int, n: int, which: str = "basic") -> list[T.Task]:
    ts = T.all_tasks(which)
    random.Random(seed).shuffle(ts)
    return ts[:n]


def score(task: T.Task, wd: Path, image: str | None) -> tuple[int, int, str]:
    name = "test_hidden_acceptance.py"
    (wd / name).write_text(task.hidden_test)
    pytest = ["python", "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", name]
    cmd = (["podman", "run", "--rm", "-v", f"{wd}:/w:Z", "-w", "/w", image, "bash", "-c",
            "pip install -q pytest >/dev/null 2>&1; " + " ".join(pytest)] if image else [sys.executable, *pytest[1:]])
    try:
        p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=300)
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        out = ""
    finally:
        (wd / name).unlink(missing_ok=True)
    passed = int(m[1]) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m[1]) if (m := re.search(r"(\d+) failed", out)) else 0
    errors = int(m[1]) if (m := re.search(r"(\d+) error", out)) else 0
    return passed, max(passed + failed + errors, task.hidden_test.count("\ndef test_")), out


def failed_tests(out: str) -> list[str]:
    return re.findall(r"^(?:FAILED|ERROR) \S+::(\w+)", out, re.M)


def house_counts(task: T.Task, out: str) -> tuple[int, int]:
    """(passed, total) of the task's house-rule checks, counted from passes (a crash passes none)."""
    total = len(re.findall(r"^def test_house_", task.hidden_test, re.M))
    return len(set(re.findall(r"^PASSED \S+::(test_house_\w+)", out, re.M))), total


def feedback(out: str) -> str:
    """What the acceptance check said, the way a user would after trying the delivery: each failing check and its
    first assertion line, not the test code."""
    fails, cur = [], None
    for line in out.splitlines():
        if line.startswith("FAILED "):
            fails.append(line.split("::")[-1].split(" - ")[0])
        elif line.startswith("E   ") and (cur is None or cur not in fails):
            cur = line[4:].strip()
            if fails or "assert" in cur.lower() or "error" in cur.lower():
                fails.append(f"({cur[:160]})")
    return "; ".join(dict.fromkeys(fails))[:600]


def tell_memory(port: int, task: T.Task, passed: int, total: int, out: str, scope: str = "",
                warning_id: int | None = None) -> None:
    """Ground truth back into memrouter after delivery (simulated owner feedback): the failing checks become
    experience for the scope (crediting the warnings this job was shown), the owner's words are kept, and a house
    rule the delivery broke is stated by the owner as a standing rule."""
    import urllib.request

    def post(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=120).read()
    ok = passed == total and total > 0
    what = "all acceptance checks passed" if ok else f"{passed}/{total} acceptance checks passed. Failed: {feedback(out)}"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memrouter"))
    import failures as FL

    post("/outcome", {"scope": scope, "failed": FL.from_pytest(out), "warning_id": warning_id,
                      "passed": re.findall(r"^PASSED \S+::(\w+)", out, re.M)})
    post("/add", {"text": f"Owner feedback after delivery of '{task.goal[:160]}': {what}", "speaker": "owner",
                  "chat": "acceptance-feedback", "detect_rule": False})
    for rule in T.broken_rules(failed_tests(out)):  # the owner states the convention the delivery broke, as a person would
        post("/rule", {"text": f"From now on, in every program you build for me: {rule}"})


def label_judge(db: Path, judge_id: int | None, outcome: bool) -> None:
    """The judge said how likely the job was done; the hidden tests say whether it was: its calibration data."""
    if judge_id:
        sys.path.insert(0, str(RUNNER.parent))
        import judge as J
        J.Judge(None, db).label(judge_id, float(outcome))


def start_memrouter(cmd: str, home: Path, port: int) -> subprocess.Popen:
    shutil.rmtree(home, ignore_errors=True)
    p = subprocess.Popen(cmd.format(home=home, port=port).split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import urllib.request
    for _ in range(90):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return p
        except Exception:
            time.sleep(2)
    raise RuntimeError(f"memrouter on {port} did not start")


def run_stream(arm: str, seed: int, args, port: int):
    out = Path(args.out)
    log = out / f"{arm}-{seed}.jsonl"
    mem = None
    if arm in ("mem", "rules"):
        mem = start_memrouter(args.memrouter_cmd, out / f"memrouter-{seed}", port)
    try:
        for pos, task in enumerate(order(seed, args.n, args.set)):
            wd = out / "work" / f"{arm}-{seed}" / f"{pos:02d}-{task.id}"
            shutil.rmtree(wd, ignore_errors=True)
            cmd = [sys.executable, str(RUNNER), task.goal, "--model-url", args.model_url, "--workdir", str(wd),
                   "--max-minutes", str(args.max_minutes), "--judge-db", str(out / "judge.db")]
            if args.local:
                cmd.append("--local")
            if mem:
                cmd += ["--memory-url", f"http://127.0.0.1:{port}", "--scope", args.set]
                if arm == "rules":
                    cmd.append("--rules-only")
            t0 = time.time()
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                proc.wait(timeout=args.max_minutes * 60 + 600)
            except subprocess.TimeoutExpired:  # a job past the hard cap is unsolved; the stream goes on
                proc.terminate()  # SIGTERM first: the runner removes its container
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
            minutes = (time.time() - t0) / 60
            passed, total, out_text = score(task, wd, args.image)
            run = [json.loads(x) for x in (wd / "run.jsonl").read_text().splitlines()] if (wd / "run.jsonl").exists() else []
            rep = next((e for e in reversed(run) if e.get("event") == "report"), {})
            if mem:
                try:
                    tell_memory(port, task, passed, total, out_text, args.set, rep.get("warning_id"))
                except Exception as e:
                    print(f"feedback to memory failed: {e!r}", flush=True)
            label_judge(out / "judge.db", rep.get("judge_id"), passed == total and total > 0)
            rec = {"arm": arm, "seed": seed, "pos": pos, "task": task.id, "family": task.family, "passed": passed,
                   "total": total, "solved": passed == total and total > 0, "minutes": round(minutes, 2),
                   "interventions": len(rep.get("decisions") or []), "runner_ok": bool(rep.get("ok")),
                   "failed_tests": failed_tests(out_text)}
            hp, ht = house_counts(task, out_text)
            if ht:
                rec.update(house_passed=hp, house_total=ht)
            with log.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            print(json.dumps(rec), flush=True)
    finally:
        if mem:
            mem.terminate()


def run(args):
    Path(args.out).mkdir(parents=True, exist_ok=True)
    streams = [(s.split(":")[0], int(s.split(":")[1])) for s in args.streams.split(",")]
    threads = [threading.Thread(target=run_stream, args=(arm, seed, args, 8770 + k))
               for k, (arm, seed) in enumerate(streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(report(args.out))


def report(out: str) -> str:
    rows = [json.loads(x) for f in Path(out).glob("*-*.jsonl") for x in f.read_text().splitlines()]
    if not rows:
        return "no results yet"
    lines = []
    by = {a: [r for r in rows if r["arm"] == a] for a in sorted({r["arm"] for r in rows})}
    for a, rs in by.items():
        n = max(r["pos"] for r in rs) + 1
        early = [r for r in rs if r["pos"] < n / 2]
        late = [r for r in rs if r["pos"] >= n / 2]
        rate = lambda xs: 100 * sum(r["solved"] for r in xs) / max(len(xs), 1)  # noqa: E731
        lines.append(f"{a:5} jobs {len(rs):3}  solved {rate(rs):5.1f}%  (early half {rate(early):5.1f}% -> late half "
                     f"{rate(late):5.1f}%)  hidden tests passed {100 * sum(r['passed'] for r in rs) / max(sum(r['total'] for r in rs), 1):5.1f}%  "
                     f"median {statistics.median(r['minutes'] for r in rs):4.1f} min  interventions/job "
                     f"{statistics.mean(r['interventions'] for r in rs):4.2f}")
        if any("house_total" in r for r in rs):  # the conventions set: did the owner's rules get learned?
            hr = lambda xs: 100 * sum(r.get("house_passed", 0) for r in xs) / max(sum(r.get("house_total", 0) for r in xs), 1)  # noqa: E731
            lines.append(f"{'':5} house-rule checks passed {hr(rs):5.1f}%  (early half {hr(early):5.1f}% -> late half "
                         f"{hr(late):5.1f}%)")
    for arm in [a for a in by if a != "none"] if "none" in by else []:  # each arm against no memory, paired by
        pairs = {(r["seed"], r["pos"]): r for r in by["none"]}           # (seed, position): same task, same place
        both = [(r, pairs[(r["seed"], r["pos"])]) for r in by[arm] if (r["seed"], r["pos"]) in pairs]
        metrics = [("solved", lambda r: int(r["solved"]))]
        if both and all("house_total" in r for pair in both for r in pair):
            house = lambda r: r["house_passed"] / max(r["house_total"], 1)  # noqa: E731
            n = max(r["pos"] for r, _ in both) + 1
            metrics += [("house-rule checks", house), ("house-rule checks, late half", house)]
        for name, f in metrics:
            sel = [p for p in both if not name.endswith("late half") or p[0]["pos"] >= n / 2]
            diffs = [f(a) - f(b) for a, b in sel]
            if diffs:
                rnd = random.Random(0)
                boots = sorted(statistics.mean(rnd.choice(diffs) for _ in diffs) for _ in range(5000))
                lines.append(f"{arm} - none, {name}, paired on {len(diffs)} jobs: {100 * statistics.mean(diffs):+.1f} pts "
                             f"[95% CI {100 * boots[125]:+.1f}, {100 * boots[4875]:+.1f}]")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--streams", default="mem:1,mem:2,none:1,none:2")
    r.add_argument("--n", type=int, default=16)
    r.add_argument("--model-url", required=True)
    r.add_argument("--max-minutes", type=float, default=10)
    r.add_argument("--image", default=None, help="score inside this container image (podman)")
    r.add_argument("--set", default="basic", choices=list(T.SETS), help="task set (house = the conventions set)")
    r.add_argument("--local", action="store_true", help="runner without a container (trusted use only)")
    r.add_argument("--memrouter-cmd", default="python memory_service.py --home {home} --port {port}")
    sub.add_parser("report").add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "run":
        run(args)
    else:
        print(report(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
