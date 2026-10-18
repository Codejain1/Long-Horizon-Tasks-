"""Tickets one at a time, in parallel: the fast loop (minutes, with repeats) next to backlog.py (hours, in order).

    python bench/tickets.py states --scenario rental [--tickets 1-4]       build and check the states
    python bench/tickets.py run --scenario ward --arms plain,runner --tickets 1-4 --reps 3 --out DIR --model-url URL
    python bench/tickets.py report --out DIR

Ticket k starts from a known-good repo: the scenario's start for ticket 1; for k > 1 the reference with every
module whose names the hidden tests first use at ticket k or later taken out (file, import line, __all__ entry),
and the start's tests, README and docs (nothing that describes later tickets).
A state counts only if it passes the hidden tests of tickets 1..k-1 and fails ticket k's: `states` checks that.
Valid states (2026-10-19): rental 1-4, 6, 7, 11; ward 1, 2, 11. The others change shared modules, so a module
can't be cut out: those tickets stay in backlog.py.
Each job is scored on ticket k's hidden tests (solved) and on tickets 1..k-1 (kept). No QA rounds by default:
"solved on the first build" is the number. What this can't measure: carrying an earlier ticket's notes forward
(each job sees only its own ticket); backlog.py measures that.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backlog as BL  # noqa: E402


def first_use(sdir: Path, pkg: str) -> dict[str, int]:
    """Each public name of the package -> the first ticket whose hidden tests use it (`pkg.name`)."""
    import ast
    conf = sdir / "hidden" / "conftest.py"
    helpers = {}  # a conftest helper's names count from the first ticket whose tests call it (ward's add_nurse)
    if conf.exists():
        src = conf.read_text()
        helpers = {n.name: set(re.findall(rf"\b{pkg}\.(\w+)", ast.get_source_segment(src, n)))
                   for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    first: dict[str, int] = {}
    for f in sorted(sdir.glob("hidden/test_s*.py"), key=lambda f: int(f.stem[6:])):
        text, k = f.read_text(), int(f.stem[6:])
        used = set(re.findall(rf"\b{pkg}\.(\w+)", text))
        used |= {x for h, xs in helpers.items() if re.search(rf"\b{h}\(", text) for x in xs}
        for name in sorted(used):
            first.setdefault(name, k)
    return first


def build_state(sdir: Path, sc: dict, k: int, dest: Path) -> Path:
    """The repo just before ticket k (see the module docstring). A module goes only if the start project doesn't
    have it, the hidden tests first use all its names at ticket k or later, and no module that stays imports it."""
    import ast
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(sdir / "start", dest)  # its tests, README and docs: nothing describing later tickets
    if k == 1:
        return dest
    pkg = sc["package"]
    base = {f.stem for f in (dest / pkg).glob("*.py")}
    shutil.rmtree(dest / pkg)
    shutil.copytree(sdir / "reference" / pkg, dest / pkg, ignore=shutil.ignore_patterns("__pycache__"))
    init = dest / pkg / "__init__.py"
    text, first = init.read_text(), first_use(sdir, pkg)
    exports = {n.module.split(".")[-1]: (n, [a.asname or a.name for a in n.names])
               for n in ast.parse(text).body if isinstance(n, ast.ImportFrom) and (n.module or "").startswith(pkg + ".")}
    later = {m for m, (_, names) in exports.items() if m not in base and all(first.get(x, 99) >= k for x in names)}

    def imports(m: str) -> set[str]:
        return set(re.findall(rf"(?:from {pkg}\.|from \.|import {pkg}\.)(\w+)", (dest / pkg / f"{m}.py").read_text()))
    while True:  # a module a staying module needs stays too
        needed = {d for m in {f.stem for f in (dest / pkg).glob("*.py")} - later - {"__init__"} for d in imports(m)}
        if not later & needed:
            break
        later -= needed
    lines = text.splitlines(keepends=True)
    for m in sorted(later, key=lambda m: -exports[m][0].lineno):
        node, names = exports[m]
        del lines[node.lineno - 1:node.end_lineno]
        (dest / pkg / f"{m}.py").unlink()
    text = "".join(lines)
    for m in later:
        for x in exports[m][1]:
            text = re.sub(rf'\s*"{re.escape(x)}",?', "", text)
    init.write_text(text)
    return dest


def check_state(sdir: Path, sc: dict, k: int, work: Path, image: str | None = None) -> tuple[bool, str]:
    repo = build_state(sdir, sc, k, work / f"state-t{k}")
    passed, failed, out = BL.hidden(sdir, k, repo, work / f"check-t{k}", image)
    mine = set(tests_of(sdir, k))
    before_ok = not (set(failed) - mine)
    open_ = bool(mine & set(failed))
    return before_ok and open_, (f"t{k}: tickets 1..{k - 1} {'pass' if before_ok else 'FAIL: ' + str(sorted(set(failed) - mine))}; "
                                 f"ticket {k} {'still open' if open_ else 'ALREADY SOLVED'} ({len(mine & set(failed))}/{len(mine)} failing)")


def tests_of(sdir: Path, k: int) -> list[str]:
    return re.findall(r"^def (test_\w+)", (sdir / "hidden" / f"test_s{k}.py").read_text(), re.M)


def tickets(spec: str) -> list[int]:
    """"1-4", "1,2,11" or "1-4,6"."""
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return out


def run(args) -> None:
    sdir, sc = BL.scenario(args.scenario)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for k in tickets(args.tickets):
        ok, why = check_state(sdir, sc, k, out / "states", args.image)
        print(why, flush=True)
        if not ok:
            raise SystemExit(f"state before ticket {k} is not valid: {why}")
    jobs = [(k, arm, r) for r in range(1, args.reps + 1) for k in tickets(args.tickets) for arm in args.arms.split(",")]
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not jobs:
                    return
                k, arm, r = jobs.pop(0)
            work = out / "work" / f"{arm}-t{k}-r{r}"
            repo = build_state(sdir, sc, k, work / "repo")
            a = argparse.Namespace(**{**vars(args), "arm": arm, "label": f"{arm}-t{k}-r{r}", "out": str(out), "extra": args.extra})
            try:
                build = BL.run_job(sc["sessions"][k - 1], repo, work / "logs", f"t{k:02d}", a, None)
                passed, failed, _ = BL.hidden(sdir, k, repo, work, args.image)
                mine = set(tests_of(sdir, k))
                rec = {"scenario": args.scenario, "ticket": k, "arm": arm, "rep": r, "minutes": build["minutes"],
                       "tokens": build["tokens"], "runner_ok": build["ok"],
                       "solved": not (mine & set(failed)) and bool(mine & set(passed)),
                       "kept": not (set(failed) - mine), "passed": sorted(passed), "failed": sorted(failed)}
            except Exception as e:  # recorded, never a silently dead worker; a crash is not a "0 solved"
                rec = {"scenario": args.scenario, "ticket": k, "arm": arm, "rep": r, "error": repr(e)[:2000]}
            with lock, (out / "results.jsonl").open("a") as f:
                f.write(json.dumps(rec) + "\n")
            print(json.dumps({x: rec.get(x) for x in ("ticket", "arm", "rep", "solved", "kept", "minutes", "error")}), flush=True)
    threads = [threading.Thread(target=worker) for _ in range(args.streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 1.0
    p, d = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def report(out: str) -> str:
    f = Path(out) / "results.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
    errors = [r for r in rows if "error" in r]
    rows = [r for r in rows if "error" not in r]
    lines = []
    for arm in sorted({r["arm"] for r in rows}):
        rs = [r for r in rows if r["arm"] == arm]
        s, kept = sum(r["solved"] for r in rs), sum(r["kept"] for r in rs)
        lo, hi = wilson(s, len(rs))
        per = "  ".join(f"t{k} {sum(r['solved'] for r in rs if r['ticket'] == k)}/{sum(r['ticket'] == k for r in rs)}"
                        for k in sorted({r["ticket"] for r in rs}))
        lines.append(f"{arm:7} solved {s}/{len(rs)} = {100 * s / len(rs):4.0f}% [{100 * lo:.0f}, {100 * hi:.0f}]  kept earlier "
                     f"tickets {kept}/{len(rs)}  minutes {sum(r['minutes'] for r in rs):6.1f}  tokens "
                     f"{sum(r['tokens'] for r in rs) / 1e6:5.2f}M   {per}")
    arms = sorted({r["arm"] for r in rows})
    if "plain" in arms:  # paired by ticket and repeat: the same work, the same draw of the model's luck
        base = {(r["ticket"], r["rep"]): r["solved"] for r in rows if r["arm"] == "plain"}
        for arm in arms:
            pairs = [(r["solved"], base[(r["ticket"], r["rep"])]) for r in rows if r["arm"] == arm and arm != "plain"
                     and (r["ticket"], r["rep"]) in base]
            if pairs:
                win, loss = sum(a and not b for a, b in pairs), sum(b and not a for a, b in pairs)
                lines.append(f"{arm} vs plain: solved only by {arm} {win}, only by plain {loss} (of {len(pairs)} pairs)"
                             + ("" if win + loss >= 6 else "  (too few disagreements to call)"))
    if errors:
        lines.append(f"HARNESS ERRORS ({len(errors)}, not scored): " + "; ".join(e["error"][:120] for e in errors[:3]))
    return "\n".join(lines) or "no results yet"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("states")
    s.add_argument("--scenario", default="rental")
    s.add_argument("--tickets", default="1-12")
    s.add_argument("--work", default="/tmp/ticket-states")
    s.add_argument("--image", default=None)
    r = sub.add_parser("run")
    r.add_argument("--scenario", default="rental")
    r.add_argument("--arms", default="plain,runner")
    r.add_argument("--tickets", default="1-4")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--streams", type=int, default=6)
    r.add_argument("--out", required=True)
    r.add_argument("--model-url", required=True)
    r.add_argument("--max-minutes", type=float, default=20)
    r.add_argument("--image", default=None)
    r.add_argument("--local", action="store_true")
    r.add_argument("--explore", type=float, default=0.0)
    r.add_argument("--extra", default="")
    p = sub.add_parser("report")
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "states":
        sdir, sc = BL.scenario(a.scenario)
        oks = [check_state(sdir, sc, k, Path(a.work), a.image) for k in tickets(a.tickets)]
        print("\n".join(w for _, w in oks))
        return 0 if all(o for o, _ in oks) else 1
    if a.cmd == "run":
        run(a)
    print(report(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
