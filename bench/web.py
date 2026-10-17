"""Web tasks without tests (docs/LONG_HORIZON.md §3, part 3): Hermes-style research and preparation jobs.

    python bench/web.py run --out DIR --model-url URL [--arms plain,runner] [--reps 2] [--streams 4]
    python bench/web.py report --out DIR

Each task's answer.json is scored against an independent source fetched at scoring time (never shown to the
agent), and its claims go through the same evidence check the runner's watcher uses: every quote must be on its
page. Arms: plain (one tool loop) and runner (--kind research: evidence checks and a calibrated judge).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "runner" / "longrun.py"
sys.path.insert(0, str(ROOT / "runner"))
import longrun as LR  # noqa: E402

HOME = (12.9353, 77.6134)  # Koramangala, Bengaluru (the owner's usual area)


def get_json(url: str, data: bytes | None = None, timeout: float = 30):
    req = urllib.request.Request(url, data, {"User-Agent": "long-horizon-bench/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def status(url: str) -> int:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


@dataclass
class WebTask:
    id: str
    goal: str
    score: object  # callable(answer: dict) -> (passed, total, notes)


def score_versions(answer: dict, fetch=get_json):
    notes, ok = [], 0
    for key, product in (("python", "python"), ("nodejs", "nodejs"), ("postgresql", "postgresql")):
        truth = str(fetch(f"https://endoflife.date/api/{product}.json")[0]["latest"])
        got = str((answer.get("answer") or {}).get(key, "")).strip().lstrip("v")
        ok += got == truth
        notes.append(f"{key}: {got or '-'} (truth {truth})")
    return ok, 3, notes


def osm_names(lat: float, lng: float, radius: int = 2500, fetch=get_json) -> set[str]:
    q = (f'[out:json][timeout:25];nwr(around:{radius},{lat},{lng})["amenity"~"^(restaurant|cafe|fast_food)$"]["name"];'
         "out tags 500;")
    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
        try:
            els = fetch(url, urllib.parse.urlencode({"data": q}).encode()).get("elements", [])
            return {e["tags"]["name"].strip().lower() for e in els}
        except Exception:
            continue
    raise RuntimeError("OpenStreetMap (Overpass) unreachable: score this task later")


def score_places(answer: dict, fetch=get_json):
    names = osm_names(*HOME, fetch=fetch)
    places = ((answer.get("answer") or {}).get("places") or [])[:3]
    found = [str(p.get("name", "")).strip().lower() in names for p in places if isinstance(p, dict)]
    return sum(found), 3, [f"{p.get('name')}: {'in OSM within 2.5 km' if f else 'NOT found'}"
                           for p, f in zip(places, found)]


def score_flights(answer: dict, check=status):
    links = [x for x in ((answer.get("answer") or {}).get("links") or []) if isinstance(x, dict)][:4]
    urls = [str(x.get("url", "")) for x in links]
    date = re.compile(r"2026-12-15|15/12/2026|12/15/2026|15-12-2026|20261215|15 ?dec|dec ?15", re.I)
    route = lambda u: re.search(r"del", u, re.I) and re.search(r"blr", u, re.I)  # noqa: E731
    checks = [len(urls) >= 2,
              any(re.search(r"google\.[a-z.]+/(travel/)?flights", u) for u in urls),
              all(0 < check(u) < 400 for u in urls) and bool(urls),
              all(route(urllib.parse.unquote(u)) and date.search(urllib.parse.unquote(u)) for u in urls) and bool(urls)]
    return sum(checks), 4, [f"links {len(urls)}", f"resolve {checks[2]}", f"route+date in every link {checks[3]}"]


FORMAT = (" Use live web sources (fetch the pages), not what you remember. Write the result to answer.json as "
          '{"answer": <the answer above>, "claims": [{"claim": <one fact>, "url": <the page it is on>, "quote": <a short '
          'phrase copied exactly from that page>}]}.')

TASKS = [
    WebTask("versions", "Find the newest released version of Python, of Node.js and of PostgreSQL (the latest stable "
                        "release of the newest release line of each), from official or reliable sources. answer = "
                        '{"python": "x.y.z", "nodejs": "x.y.z", "postgresql": "x.y"}.', score_versions),
    WebTask("places", f"The owner is at {HOME[0]}, {HOME[1]} (Koramangala, Bengaluru) and wants to eat out. Find 3 "
                      "restaurants or cafes within 2 km of that point, using OpenStreetMap data (for example the "
                      "Overpass API). answer = {\"places\": [{\"name\": ..., \"lat\": ..., \"lng\": ...}]}.",
            score_places),
    WebTask("flights", "Prepare flight search links for 1 adult from Delhi (DEL) to Bengaluru (BLR) on 15 December "
                       "2026: one Google Flights link and one on an airline's own website, so the owner can compare "
                       "and book. Do NOT book anything. Every link must open the search for that route and date. "
                       'answer = {"links": [{"site": ..., "url": ...}]}.', score_flights),
]


def run_job(task: WebTask, arm: str, rep: int, args) -> dict:
    wd = Path(args.out) / "work" / f"{arm}-{rep}" / task.id
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True)
    cmd = [sys.executable, str(RUNNER), task.goal + FORMAT, "--model-url", args.model_url, "--workdir", str(wd),
           "--max-minutes", str(args.max_minutes), "--judge-db", str(Path(args.out) / "judge.db"), "--explore", str(args.explore)]
    cmd += ["--plain"] if arm == "plain" else ["--kind", "research"]
    cmd += ["--local"] if args.local else []
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        p.wait(timeout=args.max_minutes * 60 + 600)
    except subprocess.TimeoutExpired:
        p.kill()
    events = [json.loads(x) for x in (wd / "run.jsonl").read_text().splitlines()] if (wd / "run.jsonl").exists() else []
    rep_ev = next((e for e in reversed(events) if e.get("event") == "report"), {})
    try:
        answer = json.loads((wd / "answer.json").read_text())
    except (OSError, ValueError):
        answer = {}
    try:
        passed, total, notes = task.score(answer)
    except Exception as e:  # the truth source was down: record it, never count it as the agent's failure
        passed, total, notes = None, None, [f"scoring failed: {e!r}"]
    evidence_ok, evidence = LR.Runner("", LR.LocalSandbox(wd), None, wd / "score.jsonl").check_evidence("answer.json") \
        if answer else (False, "no answer.json")
    rec = {"task": task.id, "arm": arm, "rep": rep, "passed": passed, "total": total, "notes": notes,
           "evidence_ok": evidence_ok, "evidence": evidence[:300], "minutes": round((time.time() - t0) / 60, 2),
           "runner_ok": bool(rep_ev.get("ok")), "judge_p": rep_ev.get("judge_p"),
           "tokens": sum((rep_ev.get("usage") or {}).get(k, 0) for k in ("prompt_tokens", "completion_tokens"))}
    if rep_ev.get("judge_id") and passed is not None:
        sys.path.insert(0, str(ROOT / "bench"))
        import bench as B
        B.label_judge(Path(args.out) / "judge.db", rep_ev["judge_id"], passed == total and evidence_ok)
    return rec


def run(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(t, arm, r) for r in range(1, args.reps + 1) for arm in args.arms.split(",") for t in TASKS]
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not jobs:
                    return
                t, arm, r = jobs.pop(0)
            try:
                rec = run_job(t, arm, r, args)
            except Exception as e:  # one broken job is recorded, never a silently dead worker
                rec = {"task": t.id, "arm": arm, "rep": r, "passed": None, "total": None, "notes": [f"harness error: {e!r}"],
                       "evidence_ok": False, "evidence": "", "minutes": 0, "runner_ok": False, "judge_p": None, "tokens": 0}
            with lock, (out / "results.jsonl").open("a") as f:
                f.write(json.dumps(rec) + "\n")
            print(json.dumps({k: rec[k] for k in ("task", "arm", "rep", "passed", "total", "evidence_ok")}), flush=True)
    threads = [threading.Thread(target=worker) for _ in range(args.streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def report(out: str) -> str:
    f = Path(out) / "results.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    lines = []
    for arm in sorted({r["arm"] for r in rows}):
        rs = [r for r in rows if r["arm"] == arm and r["passed"] is not None]
        done = [r for r in rs if r["passed"] == r["total"] and r["evidence_ok"]]
        lines.append(f"{arm:7} jobs {len(rs):2}  complete and verified {len(done)}/{len(rs)}  checks "
                     f"{sum(r['passed'] for r in rs)}/{sum(r['total'] for r in rs)}  evidence ok "
                     f"{sum(r['evidence_ok'] for r in rs)}/{len(rs)}  minutes {sum(r['minutes'] for r in rs):.1f}")
        for r in sorted(rs, key=lambda r: (r["task"], r["rep"])):
            lines.append(f"    {r['task']:9} rep {r['rep']}  {r['passed']}/{r['total']}  evidence {r['evidence_ok']}  "
                         f"judge {r['judge_p']}  {'; '.join(r['notes'])[:150]}")
    return "\n".join(lines) or "no results yet"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--model-url", required=True)
    r.add_argument("--arms", default="plain,runner")
    r.add_argument("--reps", type=int, default=2)
    r.add_argument("--streams", type=int, default=4)
    r.add_argument("--max-minutes", type=float, default=15)
    r.add_argument("--explore", type=float, default=0.2, help="watcher exploration for world-model data")
    r.add_argument("--local", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "run":
        run(args)
    print(report(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
