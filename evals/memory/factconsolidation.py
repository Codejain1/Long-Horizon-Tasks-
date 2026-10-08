"""MemoryAgentBench's selective-forgetting test (FactConsolidation), with its own prompt and scoring.

    python evals/memory/factconsolidation.py --arm horizon --splits sh_6k mh_6k --n 50 --out /tmp/fc

A numbered list of facts in which later facts overwrite earlier ones (often with counterfactual values); the
right answer is always the newest version, and multi-hop (mh) questions chain several updated facts. The paper
found every memory method at or under 7 % on multi-hop. Arms, with the same answering model:
- `horizon`: factmem with supersession (Jev marks replaced facts), current facts only, two-hop retrieval, Jev's
  relevance judgement.
- `rag`: the top facts by meaning, old versions included (the standard baseline).
- `full`: the whole fact list in the prompt.
Scoring: the benchmark's substring exact match (normalised). Data: huggingface ai-hyz/MemoryAgentBench,
Conflict_Resolution split, at ~/.cache/horizon-bench/mab/cr.parquet.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from longmemeval import claude  # noqa: E402

DATA = Path.home() / ".cache" / "horizon-bench" / "mab" / "cr.parquet"
# utils/templates.py, factconsolidation, rag_agent query (the long-context one is the same text).
QUERY = ("Pretend you are a knowledge management system. Each fact in the knowledge pool is provided with a serial "
         "number at the beginning, and the newer fact has larger serial number. \n You need to solve the conflicts of "
         "facts in the knowledge pool by finding the newest fact with larger serial number. You need to answer a "
         "question based on this rule. You should give a very concise answer without saying other words for the "
         "question **only** from the knowledge pool you have memorized rather than the real facts in real world. \n\n"
         "For example:\n\n [Knowledge Pool] \n\n Question: Based on the provided Knowledge Pool, what is the name of the "
         "current president of Russia? \nAnswer: Donald Trump \n\n Now Answer the Question: Based on the provided "
         "Knowledge Pool, {question} \nAnswer:")
MEMORIZED = "The following context is the facts I have learned:\n{facts}\n\n"


def normalize(s: str) -> str:
    s = s.lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def substring_match(prediction: str, answers: list[str]) -> bool:
    p = normalize(prediction)
    return any(normalize(a) in p for a in answers)


def facts_of(context: str) -> list[str]:
    return [line.strip() for line in context.splitlines() if re.match(r"^\d+\.\s", line.strip())]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["horizon", "rag", "full"], required=True)
    ap.add_argument("--splits", nargs="+", default=["sh_6k", "mh_6k"])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    import os

    import pandas as pd

    from horizon.factmem import FactMemory
    from horizon.memrouter.embedding import make_embedder

    scorer = None
    if args.arm == "horizon":
        from horizon.decision.scorers import JevScorer, typesafe_key

        os.environ.setdefault("TYPESAFE_API_KEY", typesafe_key() or "")
        scorer = JevScorer()
    embedder = make_embedder("fastembed") if args.arm != "full" else None
    table = pd.read_parquet(DATA)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for split in args.splits:
        row = next(r for _, r in table.iterrows() if str(r["metadata"]["qa_pair_ids"][0]).rsplit("_no", 1)[0]
                   == f"factconsolidation_{split}")
        facts = facts_of(row["context"])
        path = out / f"{args.arm}-{split}.jsonl"
        done = {json.loads(line)["i"] for line in path.read_text().splitlines()} if path.exists() else set()
        mem, replaced = None, 0
        if args.arm != "full":
            mem = FactMemory(embedder)
            replaced = mem.add_facts(facts, scorer=scorer)
        print(f"{args.arm} {split}: {len(facts)} facts, {replaced} superseded", flush=True)

        def one(i: int) -> dict:
            q = str(row["questions"][i])
            answers = [str(a) for a in row["answers"][i]]
            if args.arm == "full":
                pool = "\n".join(facts)
            else:
                hits = mem.search(q, scorer, k=30, keep=20, current_only=args.arm == "horizon",
                                  hops=2 if args.arm == "horizon" and split.startswith("mh") else 1)
                pool = "\n".join(h.unit.text for h in sorted(hits, key=lambda h: h.unit.id))
            answer, usage = claude(MEMORIZED.format(facts=pool) + QUERY.format(question=q), args.model)
            return {"i": i, "question": q, "answers": answers, "prediction": answer[:300],
                    "correct": substring_match(answer, answers), "prompt_chars": len(pool),
                    "cost_usd": usage.get("cost_usd", 0)}

        todo = [i for i in range(min(args.n, len(row["questions"]))) if i not in done]
        with cf.ThreadPoolExecutor(args.workers) as pool_, path.open("a") as fh:
            for f in cf.as_completed([pool_.submit(one, i) for i in todo]):
                try:
                    r = f.result()
                except SystemExit as exc:
                    print(exc, flush=True)
                    pool_.shutdown(cancel_futures=True)
                    return 1
                except Exception as exc:
                    print(f"  failed: {exc!r}", flush=True)
                    continue
                fh.write(json.dumps(r) + "\n")
                fh.flush()
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        print(f"  {split}: {sum(r['correct'] for r in rows)}/{len(rows)} = "
              f"{100 * sum(r['correct'] for r in rows) / len(rows):.0f} %, prompt {sum(r['prompt_chars'] for r in rows) / len(rows) / 4:.0f} tokens",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
