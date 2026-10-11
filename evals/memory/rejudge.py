"""Re-judge stored answers with an independent judge (Codex on a ChatGPT plan), so the answer model never grades
itself. Adds `j_codex` to each row; resumable.

    python evals/memory/rejudge.py locomo /tmp/mem-eval-v2/locomo/horizon.jsonl
    python evals/memory/rejudge.py lme /tmp/mem-eval-v2/lme/horizon.jsonl

LoCoMo uses Mem0's J prompt (categories 1-4; category 5 keeps its rule score); LongMemEval its own judge templates.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from longmemeval import DATA as LME_DATA  # noqa: E402
from longmemeval import JUDGE, codex_complete  # noqa: E402


def judge_row(bench: str, r: dict, gold: dict, model: str) -> bool:
    if bench == "locomo":
        from locomo import judge_j

        return r["correct"] if r["category"] == 5 else judge_j(r["question"], r["gold"], r["answer"], model,
                                                               codex_complete)
    q = gold[r["id"]]
    template = JUDGE["abstention" if r["abstention"] else q["question_type"] if q["question_type"] in JUDGE
                     else "plain"]
    verdict, _ = codex_complete(template.format(q["question"], q["answer"], r["answer"]), model)
    return verdict.strip().lower().startswith("yes")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bench", choices=["locomo", "lme"])
    ap.add_argument("path", type=Path)
    ap.add_argument("--model", default="default")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    rows = [json.loads(line) for line in args.path.read_text().splitlines() if line.strip()]
    gold = {q["question_id"]: q for q in json.loads(LME_DATA.read_text())} if args.bench == "lme" else {}

    def one(r: dict) -> dict:
        if "j_codex" not in r:
            try:
                r["j_codex"] = judge_row(args.bench, r, gold, args.model)
            except Exception as exc:  # left unjudged; a rerun picks it up
                print(f"  {r['id']}: {exc!r}", flush=True)
        return r

    with cf.ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(one, rows))
    args.path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    scored = [r for r in rows if "j_codex" in r and (args.bench == "lme" or r["category"] != 5)]
    own = "j" if args.bench == "locomo" else "correct"
    agree = sum(r["j_codex"] == r[own] for r in scored)
    print(f"{args.path}: codex judge {100 * sum(r['j_codex'] for r in scored) / max(len(scored), 1):.1f}% of "
          f"{len(scored)} (self-judged {100 * sum(r[own] for r in scored) / max(len(scored), 1):.1f}%, "
          f"agreement {agree}/{len(scored)})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
