#!/usr/bin/env python3
"""Score head-to-head memory runs under a directory (e.g. /tmp/h2h/r1).

    python evals/longhorizon/score_h2h.py /tmp/h2h/r1
    python evals/longhorizon/score_h2h.py /tmp/h2h          # all batches

Rule checks: hidden tests named in scenario.json `rule_checks` (last non-aborted session).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios"


def score_run(results_path: Path) -> dict | None:
    data = json.loads(results_path.read_text())
    scenario, arm = data["scenario"], data["arm"]
    rules = json.loads((SCENARIOS / scenario / "scenario.json").read_text()).get("rule_checks", {})
    sessions = [s for s in data["sessions"] if not s.get("aborted")]
    if not sessions:
        return None
    last = sessions[-1]["hidden"]
    passed, failed = set(last["passed"]), set(last["failed"])
    kept = sum(1 for name in rules if name in passed)
    return {
        "batch": results_path.parts[results_path.parts.index("h2h") + 1] if "h2h" in results_path.parts else "",
        "scenario": scenario,
        "arm": arm,
        "tickets": len(sessions),
        "aborted": any(s.get("aborted") for s in data["sessions"]),
        "rules_kept": kept,
        "rules_total": len(rules),
        "hidden": f"{len(passed)}/{len(passed) + len(failed)}",
        "cost_usd": round(sum(s.get("cost_usd", 0) for s in sessions), 3),
        "minutes": round(sum(s.get("wall_s", 0) for s in sessions) / 60, 1),
        "tokens_m": round(sum(s.get("tokens", 0) for s in sessions) / 1e6, 2),
        "missed_rules": [rules[n] for n in rules if n in failed],
    }


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "/tmp/h2h")
    paths = sorted(root.glob("*/*/*/results.json")) if root.name != "h2h" or (root / "r1").exists() else sorted(
        root.glob("**/**/results.json")
    )
    rows = [r for p in paths if (r := score_run(p))]
    if not rows:
        print(f"No results under {root}")
        return 1
    print(f"{'batch':<5} {'scenario':<7} {'arm':<7} {'tix':>3} {'rules':>9} {'hidden':>8} {'cost':>7} {'min':>6}")
    for r in rows:
        cut = " *" if r["aborted"] else ""
        print(
            f"{r['batch']:<5} {r['scenario']:<7} {r['arm']:<7} {r['tickets']:>3}{cut} "
            f"{r['rules_kept']:>2}/{r['rules_total']:<2} {r['hidden']:>8} ${r['cost_usd']:>6.2f} {r['minutes']:>5.1f}"
        )
    by_arm: dict[str, dict] = {}
    for r in rows:
        if r["aborted"]:
            continue
        k = (r["batch"], r["arm"])
        by_arm.setdefault(k, {"rules": 0, "rule_total": 0, "cost": 0.0, "min": 0.0})
        by_arm[k]["rules"] += r["rules_kept"]
        by_arm[k]["rule_total"] += r["rules_total"]
        by_arm[k]["cost"] += r["cost_usd"]
        by_arm[k]["min"] += r["minutes"]
    print("\nCombined (non-aborted runs only):")
    for (batch, arm), v in sorted(by_arm.items()):
        print(f"  {batch} {arm}: rules {v['rules']}/{v['rule_total']}, ${v['cost']:.2f}, {v['min']:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
