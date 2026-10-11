"""BEAM probing-question eval (Hugging Face Mohammadta/BEAM, 100K split).

    python evals/memory/beam.py --arm horizon --n 40 --out /tmp/beam --backend cursor

Uses ast-parsed probing_questions per conversation. Skips instruction/preference/summarization. Scoring is BEAM's:
each question's rubric items (e.g. "LLM response should state: there is contradictory information") are judged
0, 0.5 or 1 and averaged; a question passes at >= 0.5. Sessions are the chat's batches in conversation order (one
time anchor each); event ordering's Kendall tau is not computed, only its rubric.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arms import ARMS, KEEP, make_judge  # noqa: E402
from longmemeval import (  # noqa: E402
    QuotaExhausted,
    codex_complete,
    READ_CHATS,
    _rate_limited,
    auto_complete,
    claude,
    cursor_complete,
    gemini_complete,
    openai_complete,
)

READ_BEAM = READ_CHATS  # same reader layout

SKIP_CATS = frozenset({"instruction_following", "preference_following", "summarization"})


def beam_sessions(chat: list) -> list[tuple[str, list[dict]]]:
    """One session per batch, in conversation order. Only a batch's first message carries a time anchor
    ("March-15-2024"): grouping by anchor put 185 of 188 messages in one undated session, and sorting the anchors
    as text put April before March."""
    out, date = [], "unknown"
    for batch in chat:
        msgs = batch if isinstance(batch, list) else [batch]
        date = next((m["time_anchor"] for m in msgs if m.get("time_anchor")), date)
        out.append((date, [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in msgs]))
    return out


RUBRIC_JUDGE = """You are grading an AI assistant's answer against a rubric. For each rubric item, score:
1 if the response fully satisfies it, 0.5 if it partly does, 0 if it does not. Judge meaning, not wording.

Question: {question}

Response: {answer}

Rubric items:
{items}

Reply with only a JSON list of {n} numbers, one per item in order, e.g. [1, 0.5, 0]."""


def rubric_score(question: str, rubric: list[str], answer: str, model: str, complete) -> tuple[float, dict]:
    items = "\n".join(f"{i}. {r}" for i, r in enumerate(rubric, 1))
    verdict, usage = complete(RUBRIC_JUDGE.format(question=question, answer=answer, items=items, n=len(rubric)),
                              model)
    m = re.search(r"\[[^\]]*\]", verdict)
    try:
        vals = [min(1.0, max(0.0, float(v))) for v in json.loads(m.group(0))] if m else []
    except (ValueError, TypeError):
        vals = []
    return (sum(vals) / len(rubric) if vals else 0.0), usage




def iter_questions(split: str, max_conv: int, max_q: int, seed: int) -> list[dict]:
    from datasets import load_dataset

    rng = random.Random(seed)
    ds = load_dataset("Mohammadta/BEAM", split=split, streaming=True)
    items: list[dict] = []
    for i, row in enumerate(ds):
        if i >= max_conv:
            break
        sessions = beam_sessions(row["chat"])
        pq = ast.literal_eval(row["probing_questions"])
        for cat, qs in pq.items():
            if cat in SKIP_CATS:
                continue
            for j, q in enumerate(qs):
                if not q.get("rubric"):
                    continue
                items.append(
                    {
                        "id": f"{row['conversation_id']}:{cat}:{j}",
                        "conversation_id": row["conversation_id"],
                        "category": cat,
                        "question": q["question"],
                        "rubric": list(q["rubric"]),
                        "sessions": sessions,
                        "question_date": sessions[-1][0] if sessions else "",
                    }
                )
    rng.shuffle(items)
    return items[:max_q]


def history_chats(sessions: list[tuple[str, list[dict]]]) -> str:
    return "\n\n".join(
        f"### Session {i}:\nSession Date: {date}\nSession Content:\n{json.dumps(turns)}"
        for i, (date, turns) in enumerate(sessions, 1)
    )


def build_context(item: dict, arm: str, embedder, scorer) -> tuple[str, dict]:
    sessions = item["sessions"]
    if arm == "full":
        return history_chats(sessions), {"units": sum(len(s) for _, s in sessions)}
    from horizon.factmem import FactMemory

    mem = FactMemory(embedder)
    for sid, (date, turns) in enumerate(sessions, 1):
        mem.add_session(str(sid), date, turns)
    t = time.monotonic()
    hits = mem.search(item["question"], scorer, date=item.get("question_date"), keep=KEEP)
    info = {
        "units": len(mem.units),
        "kept": len(hits),
        "retrieval_s": round(time.monotonic() - t, 2),
        "top_relevance": max((h.relevance for h in hits), default=0.0),
    }
    chats = "\n\n".join(f"### Memory {i} (Session Date: {h.unit.date}):\n{h.unit.text}" for i, h in enumerate(hits, 1))
    return chats or "(no relevant chat history found)", info


def run_one(item: dict, arm: str, model: str, judge_model: str, embedder, scorer, complete,
            judge_complete=None) -> dict:
    context, info = build_context(item, arm, embedder, scorer)
    prompt = READ_BEAM.format(context, item.get("question_date", ""), item["question"])
    answer, usage = complete(prompt, model)
    score, judge_usage = rubric_score(item["question"], item["rubric"], answer, judge_model,
                                      judge_complete or complete)
    llm = judge_usage.get("llm") or usage.get("llm")
    return {
        "id": item["id"],
        "category": item["category"],
        "score": round(score, 3),
        "correct": score >= 0.5,
        "prompt_chars": len(prompt),
        **info,
        "answer": answer[-1500:],
        "cost_usd": usage.get("cost_usd", 0) + judge_usage.get("cost_usd", 0),
        **({"llm": llm} if llm else {}),
    }


def report(path: Path) -> int:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        print(f"missing rows in {path}")
        return 1
    rows = [r for r in rows if "score" in r]  # rows from the old free-text judge aren't comparable
    if not rows:
        print(f"no rubric-scored rows in {path}")
        return 1
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
    ok = sum(r["correct"] for r in rows)
    print(f"\n{path.stem}: rubric score {100 * sum(r['score'] for r in rows) / len(rows):.1f}%, "
          f"pass {ok}/{len(rows)} = {100 * ok / len(rows):.1f}%")
    for k in sorted(by):
        v = by[k]
        print(f"  {k:28} {100 * sum(r['score'] for r in v) / len(v):5.1f}%  pass {sum(r['correct'] for r in v)}/{len(v)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=ARMS, required=True)
    ap.add_argument("--n", type=int, default=40, help="max probing questions (after filter)")
    ap.add_argument("--conversations", type=int, default=25, help="max BEAM conversations to scan")
    ap.add_argument("--split", default="100K")
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", choices=["claude", "openai", "gemini", "auto", "cursor"], default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--judge", default=None)
    ap.add_argument("--judge-backend", choices=["same", "codex"], default="same")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    path = out / f"{args.arm}.jsonl"
    if args.report_only:
        return report(path)
    if args.backend == "openai":
        args.model = args.model or "gpt-4o-mini"
        args.judge = args.judge or "gpt-4o-mini"
    elif args.backend == "gemini":
        args.model = args.model or "gemini-3.8-flash"
        args.judge = args.judge or "gemini-3.8-flash"
    elif args.backend == "auto":
        args.model = args.model or "gpt-4o-mini"
        args.judge = args.judge or "gpt-4o-mini"
    elif args.backend == "cursor":
        args.model = args.model or "composer-2.5"
        args.judge = args.judge or args.model
    else:
        args.model = args.model or "sonnet"
        args.judge = args.judge or "sonnet"
    complete = {
        "openai": openai_complete,
        "gemini": gemini_complete,
        "auto": auto_complete,
        "claude": claude,
        "cursor": cursor_complete,
    }[args.backend]
    judge_complete = codex_complete if args.judge_backend == "codex" else None
    if judge_complete:
        args.judge = "default"
    items = iter_questions(args.split, args.conversations, args.n, seed=13)
    out.mkdir(parents=True, exist_ok=True)
    done = {r["id"] for r in map(json.loads, path.read_text().splitlines()) if "score" in r} if path.exists() else set()
    from horizon.memrouter.embedding import make_embedder

    embedder = make_embedder("fastembed") if args.arm != "full" else None
    scorer = make_judge(args.arm)
    todo = [q for q in items if q["id"] not in done]
    print(f"beam {args.arm} ({args.backend}): {len(todo)}/{len(items)} questions", flush=True)
    with cf.ThreadPoolExecutor(args.workers) as pool, path.open("a") as fh:
        futures = {
            pool.submit(run_one, q, args.arm, args.model, args.judge, embedder, scorer, complete, judge_complete): q
            for q in todo
        }
        for i, f in enumerate(cf.as_completed(futures), 1):
            try:
                row = f.result()
            except QuotaExhausted as exc:
                print(exc, flush=True)
                return 2
            except Exception as exc:
                if _rate_limited(exc):
                    print(exc, flush=True)
                    return 2
                print(f"  {futures[f]['id']}: {exc!r}", flush=True)
                continue
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"  [{i}/{len(todo)}] {row['category'][:20]:20} {'yes' if row['correct'] else 'no '}", flush=True)
    return report(path)


if __name__ == "__main__":
    sys.exit(main())
