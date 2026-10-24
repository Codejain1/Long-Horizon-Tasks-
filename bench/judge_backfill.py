"""Calibration data from finished benchmark runs: ask the judge about each past job, with the same evidence the
runner gives it, and label the answer with the job's known hidden-test result.

    python bench/judge_backfill.py --runs /home/hermes/fastbench2 /home/hermes/fastbench3 --db judge.db --model-url URL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runner"))
import judge as J  # noqa: E402
import longrun as LR  # noqa: E402

SKIP = {"__pycache__", ".pytest_cache", "venv", ".venv", "node_modules"}


def files_listing(wd: Path) -> str:
    out = []
    for d, dirs, names in os.walk(wd):
        dirs[:] = [x for x in dirs if x not in SKIP]
        out += [str(Path(d, n).relative_to(wd)) for n in names if n != "run.jsonl"]
    return "\n".join(sorted(out)[:60])


def jobs(run: Path):
    for f in sorted(run.glob("*-[0-9].jsonl")):
        for line in f.read_text().splitlines():
            rec = json.loads(line)
            wd = run / "work" / f"{rec['arm']}-{rec['seed']}" / f"{rec['pos']:02d}-{rec['task']}"
            log = wd / "run.jsonl"
            if not log.exists():
                continue
            events = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
            report = next((e for e in reversed(events) if e.get("event") == "report"), None)
            if report:
                checks = [e for e in events if e.get("event") == "check"]
                yield report["goal"], LR.job_evidence(report["goal"], report, checks, files_listing(wd)), rec["solved"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--model-url", required=True)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    judge = J.Judge(lambda m, **k: LR.chat(args.model_url, m, **k), args.db)
    todo = [j for run in args.runs for j in jobs(Path(run))]

    def one(job):
        _, evidence, solved = job
        judge.label(judge.ask("job_done", LR.JOB_DONE, evidence)["id"], float(solved))
    with ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(one, todo))
    print(f"{len(todo)} jobs judged and labelled")
    for r in judge.report():
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
