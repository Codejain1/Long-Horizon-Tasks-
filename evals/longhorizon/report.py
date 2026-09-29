"""Compare the arms of a long-horizon run: python evals/longhorizon/report.py OUT_DIR"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(out: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(out.glob("*/results.json"))]


def summary(run: dict) -> dict:
    s = [r for r in run["sessions"] if not r["aborted"]]
    last = s[-1] if s else None
    return {
        "sessions_run": len(s),
        "aborted": any(r["aborted"] for r in run["sessions"]),
        "hidden_passed_end": len(last["hidden"]["passed"]) if last else 0,
        "hidden_total_end": len(last["hidden"]["passed"]) + len(last["hidden"]["failed"]) if last else 0,
        "sessions_all_hidden_pass": sum(not r["hidden"]["failed"] for r in s),
        "sessions_with_violations": sum(bool(r["violations"]) for r in s),
        "violations_end": last["violations"] if last else [],
        "turns": sum(r["turns"] for r in s),
        "tool_calls": sum(r["tool_calls"] for r in s),
        "horizon_calls": sum(sum(r["horizon_calls"].values()) for r in s),
        "cost_usd": round(sum(r["cost_usd"] for r in s), 3),
        "tokens": sum(r["tokens"] for r in s),
    }


def main(argv: list[str]) -> int:
    runs = load(Path(argv[1]))
    for run in runs:
        print(f"\n== {run['scenario']} / {run['arm']} ({run['model']})")
        print("  session  hidden(pass/fail)  violations  turns  tools  horizon  cost$")
        for r in run["sessions"]:
            print(f"  {r['session']:>7}  {len(r['hidden']['passed']):>6}/{len(r['hidden']['failed']):<10}"
                  f"{len(r['violations']):>10}  {r['turns']:>5}  {r['tool_calls']:>5}  "
                  f"{sum(r['horizon_calls'].values()):>7}  {r['cost_usd']:>5}" + ("  ABORTED" if r["aborted"] else ""))
            for v in r["violations"]:
                print(f"           ! {v}")
    table = {f"{r['scenario']}/{r['arm']}": summary(r) for r in runs}
    print("\n" + json.dumps(table, indent=2))
    (Path(argv[1]) / "summary.json").write_text(json.dumps(table, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
