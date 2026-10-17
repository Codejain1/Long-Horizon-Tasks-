#!/usr/bin/env python3
"""Print Horizon memory scores next to published vendor numbers (not apples-to-apples)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def acc(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        return None
    ok = sum(r.get("correct", False) for r in rows)
    return ok, len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    meta = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
    model = meta.get("model", "see logs")

    print("\n=== Horizon (this run) ===")
    print(f"  model: {model}")
    for arm in ("horizon", "rag", "full"):
        t = acc(out / f"{arm}.jsonl")
        if t:
            print(f"  LongMemEval {arm:8} {t[0]}/{t[1]} = {100*t[0]/t[1]:.1f}%")

    fc = out / "factconsolidation"
    if fc.is_dir():
        for p in sorted(fc.glob("*.jsonl")):
            t = acc(p)
            if t:
                print(f"  FactConsolidation {p.stem:20} {t[0]}/{t[1]} = {100*t[0]/t[1]:.1f}%")

    for bench, label in (("locomo", "LoCoMo"), ("beam", "BEAM")):
        sub = out / bench
        if sub.is_dir():
            for arm in ("horizon", "rag", "full"):
                t = acc(sub / f"{arm}.jsonl")
                if t:
                    print(f"  {label} {arm:8} {t[0]}/{t[1]} = {100*t[0]/t[1]:.1f}%")

    sb = out / "state_bench" / "summary.json"
    if sb.is_file():
        s = json.loads(sb.read_text())
        print(f"  STATE-Bench       pass@1 {s.get('pass_at_1', '?')}/{s.get('tasks_scored', '?')} ({s.get('domain', '')})")
    elif (out / "state_bench" / "status.json").is_file():
        print("  STATE-Bench       skipped (set RUN_STATE_BENCH=1 + eval client)")

    db = out / "dolphinbench" / "smoke.json"
    if db.is_file():
        print(f"  DolphinBench      smoke OK ({json.loads(db.read_text()).get('tests_in_release', 600)} tests in release)")

    print("\n=== Industry (published; different protocol) ===")
    print("  LongMemEval-S 500q  Mem0 ~94.4%  Zep ~71–90%  paper GPT-4o full ~60%")
    print("  LoCoMo            Mem0 ~92.5%  Letta grep ~74%")
    print("  MemoryAgentBench  forgetting ≤7% all methods (FactConsolidation mh_6k is the hard slice)")
    print("  BEAM 1M           Mem0 ~64% at 1M tokens  |  STATE pass^5 ~30% travel (no memory)")
    print("  DolphinBench      action accuracy vs cost Pareto (600 tool tasks)")
    print("\nCompare only with same n, judge, and answer model. See docs/MEMORY_BENCHMARKS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
