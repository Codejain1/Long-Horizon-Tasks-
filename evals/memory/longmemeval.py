"""LongMemEval-S with the benchmark's own reader and judge prompts, answered and judged through Claude Code.

    python evals/memory/longmemeval.py --arm horizon --n 60 --out /tmp/lme      (stratified sample; --n 500 = all)

Arms, all with the same answering model and judge:
- `horizon`: horizon.factmem, candidates by meaning and words, Jev judges each one's relevance.
- `rag`: plain retrieval, the top units by meaning (the standard baseline).
- `full`: the whole chat history in the prompt (long context, about 120K tokens).
Data: ~/.cache/horizon-bench/longmemeval/longmemeval_s_cleaned.json (huggingface xiaowu0162/longmemeval-cleaned).
The answering model and judge here are Claude models through the subscription, so scores are comparable across
these arms, not to published GPT-4o numbers.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

DATA = Path.home() / ".cache" / "horizon-bench" / "longmemeval" / "longmemeval_s_cleaned.json"

# The benchmark's reader prompts (src/generation/run_generation.py), step-by-step variants.
READ_CHATS = ("I will give you several history chats between you and a user. Please answer the question based on the "
              "relevant chat history. Answer the question step by step: first extract all the relevant information, "
              "and then reason over the information to get the answer.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}"
              "\nQuestion: {}\nAnswer (step by step):")

# The benchmark's judge prompts (src/evaluation/evaluate_qa.py), verbatim.
_PLAIN = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the response "
          "contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or "
          "contains all the intermediate steps to get the correct answer, you should also answer yes. If the response "
          "only contains a subset of the information required by the answer, answer no. ")
JUDGE = {
    "plain": _PLAIN + "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? "
                      "Answer yes or no only.",
    "temporal-reasoning": _PLAIN + "In addition, do not penalize off-by-one errors for the number of days. If the "
                          "question asks for the number of days/weeks/months, etc., and the model makes off-by-one "
                          "errors (e.g., predicting 19 days when the answer is 18), the model's response is still "
                          "correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model "
                          "response correct? Answer yes or no only.",
    "knowledge-update": "I will give you a question, a correct answer, and a response from a model. Please answer yes "
                        "if the response contains the correct answer. Otherwise, answer no. If the response contains "
                        "some previous information along with an updated answer, the response should be considered as "
                        "correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect "
                        "Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "single-session-preference": "I will give you a question, a rubric for desired personalized response, and a "
                                 "response from a model. Please answer yes if the response satisfies the desired "
                                 "response. Otherwise, answer no. The model does not need to reflect all the points in "
                                 "the rubric. The response is correct as long as it recalls and utilizes the user's "
                                 "personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}"
                                 "\n\nIs the model response correct? Answer yes or no only.",
    "abstention": "I will give you an unanswerable question, an explanation, and a response from a model. Please answer "
                  "yes if the model correctly identifies the question as unanswerable. The model could say that the "
                  "information is incomplete, or some other information is given but the asked information is not."
                  "\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the "
                  "question as unanswerable? Answer yes or no only.",
}


def claude(prompt: str, model: str) -> tuple[str, dict]:
    """One plain completion through Claude Code: no tools, no project settings, nothing persisted."""
    for attempt in range(3):
        proc = subprocess.run(["claude", "-p", "--model", model, "--tools", "", "--setting-sources", "",
                               "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"],
                              input=prompt, capture_output=True, text=True, timeout=900, cwd="/tmp")
        try:
            d = json.loads(proc.stdout)
            if d.get("is_error") or not d.get("result"):
                raise ValueError(str(d.get("result"))[:200])
            return str(d["result"]), {"cost_usd": d.get("total_cost_usd") or 0, **(d.get("usage") or {})}
        except ValueError as exc:
            if "limit" in str(exc).lower():
                raise SystemExit(f"usage limit: {exc}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"claude failed: {proc.stdout[-300:]} {proc.stderr[-300:]}")


def sample(data: list[dict], n: int, seed: int = 7) -> list[dict]:
    """Stratified by question type (abstention questions spread with their type), stable across arms."""
    if n >= len(data):
        return data
    by = defaultdict(list)
    for q in data:
        by[q["question_type"]].append(q)
    rng = random.Random(seed)
    out = []
    for kind, qs in sorted(by.items()):
        rng.shuffle(qs)
        out += qs[:max(1, round(n * len(qs) / len(data)))]
    return out[:n]


def history_chats(sessions: list[tuple[str, list[dict]]]) -> str:
    """The benchmark's session format: a header with the date, then the turns as JSON."""
    return "\n\n".join(f"### Session {i}:\nSession Date: {date}\nSession Content:\n{json.dumps(turns)}"
                       for i, (date, turns) in enumerate(sessions, 1))


def build_context(q: dict, arm: str, embedder, scorer) -> tuple[str, dict]:
    sessions = list(zip(q["haystack_dates"], q["haystack_sessions"]))
    if arm == "full":
        return history_chats(sessions), {"units": sum(len(s) for _, s in sessions)}
    from horizon.factmem import FactMemory

    mem = FactMemory(embedder)
    for sid, (date, turns) in zip(q["haystack_session_ids"], sessions):
        mem.add_session(sid, date, turns)
    t = time.monotonic()
    hits = mem.search(q["question"], scorer if arm == "horizon" else None, date=q["question_date"],
                      keep=12 if arm == "horizon" else 10)
    info = {"units": len(mem.units), "kept": len(hits), "retrieval_s": round(time.monotonic() - t, 2),
            "top_relevance": max((h.relevance for h in hits), default=0.0)}
    chats = "\n\n".join(f"### Memory {i} (Session Date: {h.unit.date}):\n{h.unit.text}" for i, h in enumerate(hits, 1))
    return chats or "(no relevant chat history found)", info


def run_one(q: dict, arm: str, model: str, judge_model: str, embedder, scorer) -> dict:
    context, info = build_context(q, arm, embedder, scorer)
    prompt = READ_CHATS.format(context, q["question_date"], q["question"])
    answer, usage = claude(prompt, model)
    abstain = q["question_id"].endswith("_abs")
    template = JUDGE["abstention" if abstain else q["question_type"] if q["question_type"] in JUDGE else "plain"]
    verdict, judge_usage = claude(template.format(q["question"], q["answer"], answer), judge_model)
    return {"id": q["question_id"], "type": q["question_type"], "abstention": abstain,
            "correct": verdict.strip().lower().startswith("yes"), "prompt_chars": len(prompt), **info,
            "answer": answer[-1500:], "cost_usd": usage.get("cost_usd", 0) + judge_usage.get("cost_usd", 0)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["horizon", "rag", "full"], required=True)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--judge", default="sonnet")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    data = sample(json.loads(DATA.read_text()), args.n)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.arm}.jsonl"
    done = {json.loads(line)["id"] for line in path.read_text().splitlines()} if path.exists() else set()
    from horizon.memrouter.embedding import make_embedder

    embedder = make_embedder("fastembed") if args.arm != "full" else None
    scorer = None
    if args.arm == "horizon":
        import os

        from horizon.decision.scorers import JevScorer, typesafe_key

        os.environ.setdefault("TYPESAFE_API_KEY", typesafe_key() or "")
        scorer = JevScorer()
    todo = [q for q in data if q["question_id"] not in done]
    print(f"{args.arm}: {len(todo)} of {len(data)} questions to run", flush=True)
    with cf.ThreadPoolExecutor(args.workers) as pool, path.open("a") as fh:
        futures = {pool.submit(run_one, q, args.arm, args.model, args.judge, embedder, scorer): q for q in todo}
        for i, f in enumerate(cf.as_completed(futures), 1):
            try:
                row = f.result()
            except SystemExit as exc:
                print(exc, flush=True)
                pool.shutdown(cancel_futures=True)
                return 1
            except Exception as exc:  # one question failing doesn't stop the run
                print(f"  {futures[f]['question_id']}: {exc!r}", flush=True)
                continue
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"  [{i}/{len(todo)}] {row['type']:26} {'yes' if row['correct'] else 'no '} "
                  f"{row['prompt_chars'] // 4:>7} tok", flush=True)
    return report(path)


def report(path: Path) -> int:
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    by = defaultdict(list)
    for r in rows:
        by["abstention" if r["abstention"] else r["type"]].append(r["correct"])
    print(f"\n{path.stem}: {sum(r['correct'] for r in rows)}/{len(rows)} = {100 * sum(r['correct'] for r in rows) / len(rows):.1f} %")
    for k, v in sorted(by.items()):
        print(f"  {k:26} {sum(v)}/{len(v)}")
    print(f"  mean prompt: {sum(r['prompt_chars'] for r in rows) / len(rows) / 4:.0f} tokens; "
          f"cost ${sum(r['cost_usd'] for r in rows):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
