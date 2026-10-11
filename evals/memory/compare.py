"""Compare the arms of one benchmark on the questions every arm answered, with Horizon's gap to each.

    python evals/memory/compare.py /tmp/mem-eval-v3/locomo          (reads <arm>.jsonl for every arm present)
    python evals/memory/compare.py /tmp/mem-eval-v3/fc:-mh_6k       (FactConsolidation: <arm>-mh_6k.jsonl)

A question that failed in one arm is left out of every arm, so no arm is scored on an easier set. Each gap has a
95 % bootstrap interval and an exact McNemar p-value (only questions the two arms disagree on count). LoCoMo is
scored by J on categories 1-4 (category 5 has its own line); the others by `correct`.
"""

from __future__ import annotations

import json
import random
import sys
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arms import ARMS  # noqa: E402


def value(r: dict) -> bool:
    return bool(r["j"] if "j" in r else r["correct"])


def load(path: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {str(r.get("id", r.get("i"))): r for r in rows if "score" in r or "j" in r or "correct" in r}


def mcnemar(wins: int, losses: int) -> float:
    n = wins + losses
    if not n:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(wins, losses) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def gap_interval(a: list[bool], b: list[bool], draws: int = 10000, seed: int = 0) -> tuple[float, float]:
    rng, n = random.Random(seed), len(a)
    diffs = sorted(sum(a[i] - b[i] for i in (rng.randrange(n) for _ in range(n))) / n for _ in range(draws))
    return diffs[int(0.025 * draws)], diffs[int(0.975 * draws)]


def compare(folder: Path, suffix: str = "") -> str:
    arms = {arm: load(folder / f"{arm}{suffix}.jsonl") for arm in ARMS if (folder / f"{arm}{suffix}.jsonl").exists()}
    if not arms:
        return f"{folder}: no arms"
    shared = sorted(set.intersection(*(set(rows) for rows in arms.values())))
    cat5 = [i for i in shared if arms[next(iter(arms))][i].get("category") == 5]
    main = [i for i in shared if i not in cat5]
    lines = [f"{folder.name}{suffix}: {len(main)} questions answered by every arm "
             f"({', '.join(f'{a} {len(r)}' for a, r in arms.items())} rows)"]
    for arm, rows in arms.items():
        ok = sum(value(rows[i]) for i in main)
        extra = f"   never mentioned {sum(value(rows[i]) for i in cat5)}/{len(cat5)}" if cat5 else ""
        lines.append(f"  {arm:8} {ok:4}/{len(main)} = {100 * ok / max(len(main), 1):5.1f} %{extra}")
    if "horizon" in arms and main:
        h = [value(arms["horizon"][i]) for i in main]
        for arm, rows in arms.items():
            if arm == "horizon":
                continue
            o = [value(rows[i]) for i in main]
            wins = sum(x and not y for x, y in zip(h, o))
            losses = sum(y and not x for x, y in zip(h, o))
            lo, hi = gap_interval(h, o)
            lines.append(f"  horizon - {arm:8} {100 * (sum(h) - sum(o)) / len(main):+6.1f} pts  95 % CI "
                         f"[{100 * lo:+.1f}, {100 * hi:+.1f}]  wins {wins} losses {losses}  p = {mcnemar(wins, losses):.3f}")
    return "\n".join(lines)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        folder, _, suffix = arg.partition(":")
        print(compare(Path(folder), suffix))
