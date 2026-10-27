"""LoCoMo long-conversation QA (snap-research/LoCoMo locomo10.json).

    python evals/memory/locomo.py --arm horizon --n 80 --out /tmp/locomo --backend cursor --model composer-2.5

Arms match longmemeval.py. Scoring: LoCoMo token-F1 (category-aware), same as the official task_eval script, and
J, the LLM judge Mem0 and others report (generous CORRECT/WRONG against the gold answer; categories 1-4, as they
leave out category 5). Category 5 (adversarial) uses the official two-option prompt: "Not mentioned in the
conversation" against the trap answer. `--rejudge` adds J to rows answered before it existed.
Data: ~/.cache/horizon-bench/locomo/locomo10.json (auto-download).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from locomo_scoring import score_locomo  # noqa: E402
from arms import ARMS, EXTERNAL_ARMS, KEEP, make_judge  # noqa: E402
import mem0_arm  # noqa: E402
from longmemeval import (  # noqa: E402
    QuotaExhausted,
    codex_complete,
    _rate_limited,
    auto_complete,
    claude,
    cursor_complete,
    gemini_complete,
    openai_complete,
)

MEM0 = None  # mem0_arm.Mem0Stores, set in main for the mem0 arm
DATA = Path.home() / ".cache" / "horizon-bench" / "locomo" / "locomo10.json"
URL = "https://raw.githubusercontent.com/snap-research/LoCoMo/main/data/locomo10.json"

CONV_START = (
    "Below is a conversation between two people: {} and {}. The conversation takes place over multiple "
    "days and the date of each conversation is written at the beginning of the conversation.\n\n"
)
QA_PROMPT = (
    "\n\nBased on the above context, write an answer in the form of a short phrase for the following "
    "question. Answer with exact words from the context whenever possible.\n\nQuestion: {} Short answer:"
)
QA_PROMPT_CAT_5 = (
    "\n\nBased on the above context, answer the following question.\n\nQuestion: {} Short answer:"
)
NOT_MENTIONED = "Not mentioned in the conversation"
# Mem0's evaluation/metrics/llm_judge.py prompt.
JUDGE_J = """Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold}
Generated answer: {answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label"."""


def cat5_question(item: dict) -> tuple[str, dict]:
    """The official adversarial prompt: two options in a fixed per-question random order; returns the question
    text and the letter -> option map, so an answer of just "(a)" can be read back."""
    opts = [NOT_MENTIONED, item.get("adversarial_answer") or "unknown"]
    random.Random(item["id"]).shuffle(opts)
    return f"{item['question']} Select the correct answer: (a) {opts[0]} (b) {opts[1]}. ", {"a": opts[0], "b": opts[1]}


def read_option(answer: str, options: dict) -> str:
    a = answer.strip().lower().lstrip("(").rstrip(").")
    return options.get(a[:1], answer) if len(a) <= 2 or a[1:2] in ")." else answer


def judge_j(question: str, gold: str, answer: str, model: str, complete) -> bool:
    verdict, _ = complete(JUDGE_J.format(question=question, gold=gold, answer=answer), model)
    v = verdict.upper()
    return "CORRECT" in v and "WRONG" not in v.split("CORRECT")[-1]


def ensure_data() -> None:
    if DATA.is_file():
        return
    DATA.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=120) as resp:
        DATA.write_bytes(resp.read())


def locomo_sessions(conv: dict) -> list[tuple[str, list[dict]]]:
    out: list[tuple[str, list[dict]]] = []
    i = 1
    a, b = conv["speaker_a"], conv["speaker_b"]
    while f"session_{i}" in conv:
        date = conv.get(f"session_{i}_date_time", f"session {i}")
        turns = []
        for t in conv[f"session_{i}"]:
            speaker = t.get("speaker", a)
            role = "user" if speaker == a else "assistant"
            turns.append({"role": role, "content": t.get("text", ""), "name": speaker})
        out.append((date, turns))
        i += 1
    return out


def history_text(sessions: list[tuple[str, list[dict]]]) -> str:
    blocks = []
    for date, turns in sessions:
        lines = [f"DATE: {date}"] + [f"{t['role']}: {t['content']}" for t in turns]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def flatten(data: list[dict]) -> list[dict]:
    items = []
    for sample in data:
        sid = sample["sample_id"]
        sessions = locomo_sessions(sample["conversation"])
        sp = sample["conversation"]
        header = CONV_START.format(sp["speaker_a"], sp["speaker_b"])
        for qa in sample["qa"]:
            # Category 5 (adversarial) has no gold answer — only adversarial_answer (a trap).
            items.append(
                {
                    "id": f"{sid}:{qa['question'][:48]}",
                    "sample_id": sid,
                    "question": qa["question"],
                    "answer": str(qa.get("answer", "")),
                    "adversarial_answer": qa.get("adversarial_answer", ""),
                    "category": int(qa["category"]),
                    "sessions": sessions,
                    "header": header,
                }
            )
    return items


def sample_items(items: list[dict], n: int, seed: int = 11) -> list[dict]:
    if n >= len(items):
        return items
    by: dict[int, list[dict]] = defaultdict(list)
    for it in items:
        by[it["category"]].append(it)
    rng = random.Random(seed)
    out: list[dict] = []
    for cat in sorted(by):
        qs = by[cat]
        rng.shuffle(qs)
        out += qs[: max(1, round(n * len(qs) / len(items)))]
    rng.shuffle(out)
    return out[:n]


def build_context(item: dict, arm: str, embedder, scorer) -> tuple[str, dict]:
    sessions = item["sessions"]
    if arm == "full":
        return item["header"] + history_text(sessions), {"units": sum(len(s) for _, s in sessions)}
    if arm == "mem0":
        t = time.monotonic()
        hits = MEM0.search(item["sample_id"], sessions, item["question"], KEEP)
        info = {"kept": len(hits), "retrieval_s": round(time.monotonic() - t, 2)}
        return item["header"] + (mem0_arm.context_body(hits) or "(no relevant history)"), info
    from horizon.factmem import FactMemory

    mem = FactMemory(embedder)
    for i, (date, turns) in enumerate(sessions, 1):
        mem.add_session(f"s{i}", date, turns)
    t = time.monotonic()
    hits = mem.search(item["question"], scorer, keep=KEEP)
    info = {
        "units": len(mem.units),
        "kept": len(hits),
        "retrieval_s": round(time.monotonic() - t, 2),
        "top_relevance": max((h.relevance for h in hits), default=0.0),
    }
    body = "\n\n".join(f"DATE: {h.unit.date}\n{h.unit.text}" for h in hits)
    return item["header"] + (body or "(no relevant history)"), info


def run_one(item: dict, arm: str, model: str, embedder, scorer, complete, judge_model: str,
            judge_complete=None) -> dict:
    context, info = build_context(item, arm, embedder, scorer)
    if item["category"] == 5:
        question, options = cat5_question(item)
        prompt = context + QA_PROMPT_CAT_5.format(question)
        answer, usage = complete(prompt, model)
        answer = read_option(answer, options)
    else:
        prompt = context + QA_PROMPT.format(item["question"])
        answer, usage = complete(prompt, model)
    correct, f1 = score_locomo(answer, item["answer"], item["category"])
    j = correct if item["category"] == 5 else judge_j(item["question"], item["answer"], answer, judge_model,
                                                      judge_complete or complete)
    return {
        "id": item["id"],
        "sample_id": item["sample_id"],
        "category": item["category"],
        "question": item["question"],
        "gold": item["answer"],
        "correct": correct,
        "j": j,
        "f1": round(f1, 4),
        "prompt_chars": len(prompt),
        **info,
        "answer": answer[-1500:],
        "cost_usd": usage.get("cost_usd", 0),
        **({"llm": usage.get("llm")} if usage.get("llm") else {}),
    }


def report(path: Path) -> int:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        print(f"missing rows in {path}")
        return 1
    by: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r["f1"])
    ok = sum(r["correct"] for r in rows)
    judged = [r for r in rows if r["category"] != 5 and "j" in r]
    j = f"  J (cat 1-4) = {100 * sum(r['j'] for r in judged) / len(judged):.1f}% of {len(judged)}" if judged else ""
    print(f"\n{path.stem}: {ok}/{len(rows)} pass@F1>=0.5 = {100 * ok / len(rows):.1f}%  "
          f"mean F1 = {100 * sum(r['f1'] for r in rows) / len(rows):.1f}%{j}")
    for cat in sorted(by):
        vals = by[cat]
        jc = [r["j"] for r in rows if r["category"] == cat and "j" in r]
        print(f"  category {cat:2}  mean F1 {100 * sum(vals) / len(vals):.1f}%  "
              f"{'J ' + format(100 * sum(jc) / len(jc), '.1f') + '%  ' if jc else ''}n={len(vals)}")
    lat = sorted(r["retrieval_s"] for r in rows if "retrieval_s" in r)
    if lat:
        print(f"  retrieval p50 {lat[len(lat) // 2]:.2f}s, prompt {sum(r['prompt_chars'] for r in rows) / len(rows) / 4:.0f} tokens")
    return 0


def rejudge(path: Path, by_id: dict, judge_model: str, complete, workers: int) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r["category"] != 5 or "j" in r]  # old category-5 rows lacked the options

    def one(r: dict) -> dict:
        if "j" not in r:
            it = by_id[r["id"]]
            r.update(question=it["question"], gold=it["answer"],
                     j=judge_j(it["question"], it["answer"], r["answer"], judge_model, complete))
        return r

    with cf.ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(one, rows))
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"rejudged {path}: {len(rows)} rows kept", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=ARMS + EXTERNAL_ARMS, required=True)
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", choices=["claude", "openai", "gemini", "auto", "cursor"], default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--judge", default=None, help="J judge model (default: the answer model)")
    ap.add_argument("--judge-backend", choices=["same", "codex"], default="same")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--rejudge", action="store_true", help="add J to old rows; drop old category-5 rows (re-asked)")
    args = ap.parse_args(argv)
    out = Path(args.out)
    path = out / f"{args.arm}.jsonl"
    if args.report_only:
        return report(path)
    if args.backend == "openai":
        args.model = args.model or "gpt-4o-mini"
    elif args.backend == "gemini":
        args.model = args.model or "gemini-3.8-flash"
    elif args.backend == "auto":
        args.model = args.model or "gpt-4o-mini"
    elif args.backend == "cursor":
        args.model = args.model or "composer-2.5"
    else:
        args.model = args.model or "sonnet"
    complete = {
        "openai": openai_complete,
        "gemini": gemini_complete,
        "auto": auto_complete,
        "claude": claude,
        "cursor": cursor_complete,
    }[args.backend]
    args.judge = args.judge or args.model
    judge_complete = codex_complete if args.judge_backend == "codex" else None
    if judge_complete:
        args.judge = "default"
    ensure_data()
    if args.rejudge and path.exists():
        rejudge(path, {it["id"]: it for it in flatten(json.loads(DATA.read_text()))}, args.judge, complete,
                args.workers)
    if args.n <= 0:
        print("n must be positive", flush=True)
        return 1
    items = sample_items(flatten(json.loads(DATA.read_text())), args.n)
    out.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["id"] for line in path.read_text().splitlines()} if path.exists() else set()
    from horizon.memrouter.embedding import make_embedder

    embedder = make_embedder("fastembed") if args.arm not in ("full", "mem0") else None
    if args.arm == "mem0":
        global MEM0
        MEM0 = mem0_arm.Mem0Stores(out / "mem0-store", complete, args.model)
    scorer = make_judge(args.arm)
    todo = [q for q in items if q["id"] not in done]
    print(
        f"locomo {args.arm} ({args.backend} {args.model}): {len(todo)}/{len(items)} questions",
        flush=True,
    )
    with cf.ThreadPoolExecutor(args.workers) as pool, path.open("a") as fh:
        futures = {pool.submit(run_one, q, args.arm, args.model, embedder, scorer, complete, args.judge,
                               judge_complete): q for q in todo}
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
            print(
                f"  [{i}/{len(todo)}] cat{row['category']} f1={row['f1']:.2f} "
                f"{'ok' if row['correct'] else 'no '}",
                flush=True,
            )
    return report(path)


if __name__ == "__main__":
    sys.exit(main())
