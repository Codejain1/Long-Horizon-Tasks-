"""Judge lab: the same LoCoMo memory and candidates, judged by different judges, graded on retrieval alone.

    PYTHONPATH=src python evals/memory/judge_lab.py --judge jev --out ~/horizon-evals/judge-lab

LoCoMo labels the turns that hold each answer (`evidence`, e.g. "D1:3"), so a judge is graded directly on
whether it kept them: no answering model, no grader, no grading noise. Every judge sees the same factmem store
(built once per conversation) and the same candidates; only the judging changes. Judges:
- `none`: the top by meaning, unjudged (the `rag` arm);
- `reranker`: the local cross-encoder (the `hybrid` arm);
- `jev`: TypeSafe's Jev (the `horizon` arm);
- `llm`: the same yes/no relevance questions answered by an LLM through the Cursor CLI (`--llm-model`).
Per question: evidence recall (share of evidence turns kept), whether all were kept, how many memories were kept,
retrieval seconds, and for scoring judges every candidate's score with whether it holds evidence (calibration).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import locomo  # noqa: E402
from arms import KEEP, make_judge  # noqa: E402

JUDGES = ["none", "reranker", "jev", "llm"]

LLM_PROMPT = """You judge which stored memories are needed to answer a question about a conversation.
For each memory below, give the probability (0 to 1) that it contains information needed to answer the question:
a fact, event, date, number, preference or earlier answer the answer depends on, including an older or newer
version of it.

Question (asked on {asked_on}): {question}

Memories:
{memories}

Reply with only a JSON object mapping each memory id to its probability, e.g. {{"m0": 0.9, "m1": 0.05}}."""


class LLMJudge:
    """Factmem's relevance questions answered by an LLM in one call: one probability per memory."""

    def __init__(self, model: str):
        from longmemeval import cursor_complete

        self.model, self.complete = model, cursor_complete

    def ask(self, state: dict, questions: dict) -> dict:
        from horizon.decision.scorers import Answer

        if "memories" not in state:
            raise ValueError("the LLM judge only judges relevance")
        mems = "\n".join(f"m{j} [{m['date']}]: {m['text'][:1500]}" for j, m in enumerate(state["memories"]))
        text, _ = self.complete(LLM_PROMPT.format(asked_on=state.get("asked_on") or "unknown",
                                                  question=state["question"], memories=mems), self.model)
        match = re.search(r"\{.*\}", text, re.S)
        raw = json.loads(match.group(0)) if match else {}
        return {qid: Answer(max(0.0, min(1.0, float(raw.get(qid, 0.0))))) for qid in questions}


def make(judge: str, llm_model: str):
    if judge == "jev":
        return make_judge("horizon")
    if judge == "reranker":
        return make_judge("hybrid")
    if judge == "llm":
        return LLMJudge(llm_model)
    return None


def rounds(turns: list[dict]) -> list[list[dict]]:
    """The rounds factmem splits a session into (a user turn and what follows it), same rule as `add_session`."""
    out, current = [], []
    for t in turns:
        if t.get("role") == "user" and current:
            out.append(current)
            current = []
        current.append(t)
    return out + [current] if current else out


def build(conv: dict, embedder):
    """One conversation's factmem store, and each unit's LoCoMo turn ids."""
    from horizon.factmem import FactMemory

    mem, dia = FactMemory(embedder), []
    a = conv["speaker_a"]
    i = 1
    while f"session_{i}" in conv:
        turns = [{"role": "user" if t.get("speaker", a) == a else "assistant", "content": t.get("text", ""),
                  "dia_id": t.get("dia_id")} for t in conv[f"session_{i}"]]
        mem.add_session(f"s{i}", conv.get(f"session_{i}_date_time", f"session {i}"), turns)
        dia += [{t["dia_id"] for t in r} for r in rounds(turns)]
        i += 1
    assert len(dia) == len(mem.units), "unit ↔ turn mapping out of step with factmem's rounds"
    return mem, dia


class Recorder:
    """Wraps a judge to keep every candidate's score (for calibration)."""

    def __init__(self, inner):
        self.inner, self.last = inner, None

    def ask(self, state, questions):
        answers = self.inner.ask(state, questions)
        self.last = [answers[f"m{j}"].value for j in range(len(state.get("memories", [])))]
        return answers


def run_one(item: dict, mem, dia: list[set], judge) -> dict:
    evidence = {e.strip() for e in item["evidence"] for e in str(e).split(";")} if item["evidence"] else set()
    rec = Recorder(judge) if judge is not None else None
    t = time.monotonic()
    hits = mem.search(item["question"], rec, keep=KEEP)
    secs = time.monotonic() - t
    kept = set().union(*(dia[h.unit.id] for h in hits)) if hits else set()
    row = {"id": item["id"], "category": item["category"], "evidence": sorted(evidence), "kept": len(hits),
           "recall": (len(evidence & kept) / len(evidence)) if evidence else None,
           "all_found": evidence <= kept if evidence else None, "retrieval_s": round(secs, 2)}
    if rec is not None and rec.last is not None:
        cands = mem.candidates(item["question"])
        row["scores"] = [[round(s, 3), bool(dia[c] & evidence)] for s, c in zip(rec.last, cands)]
    return row


def report(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    graded = [r for r in rows if r["recall"] is not None]
    if not graded:
        print(f"{path.stem}: no graded rows")
        return
    lat = sorted(r["retrieval_s"] for r in rows)
    line = (f"{path.stem:9} n={len(graded):4}  evidence recall {100 * sum(r['recall'] for r in graded) / len(graded):5.1f}%"
            f"  all found {100 * sum(r['all_found'] for r in graded) / len(graded):5.1f}%"
            f"  kept {sum(r['kept'] for r in rows) / len(rows):4.1f}  p50 {lat[len(lat) // 2]:.2f}s")
    pairs = [p for r in rows for p in r.get("scores", [])]
    if pairs:
        brier = sum((s - y) ** 2 for s, y in pairs) / len(pairs)
        bins = [[p for p in pairs if lo <= p[0] < lo + 0.2 or (lo == 0.8 and p[0] == 1.0)] for lo in (0, .2, .4, .6, .8)]
        cal = " ".join(f"{lo:.1f}-{lo + .2:.1f}:{100 * sum(y for _, y in b) / len(b):3.0f}%({len(b)})"
                       for lo, b in zip((0, .2, .4, .6, .8), bins) if b)
        line += f"\n          calibration Brier {brier:.3f} | share holding evidence per score band: {cal}"
    print(line)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", choices=JUDGES, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=150, help="the same stratified sample as locomo.py; 0 = every question")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--llm-model", default="gemini-3.7-flash-high")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.judge}.jsonl"
    if args.report_only:
        report(path)
        return 0
    locomo.ensure_data()
    data = json.loads(locomo.DATA.read_text())
    items = locomo.flatten(data)
    evidence = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data for qa in s["qa"]}
    for it in items:
        it["evidence"] = evidence.get(it["id"], [])
    items = locomo.sample_items(items, args.n) if args.n else items
    done = {json.loads(line)["id"] for line in path.read_text().splitlines()} if path.exists() else set()
    todo = [it for it in items if it["id"] not in done]
    from horizon.memrouter.embedding import make_embedder

    embedder = make_embedder("fastembed")
    judge = make(args.judge, args.llm_model)
    stores = {s["sample_id"]: build(s["conversation"], embedder) for s in data
              if any(it["sample_id"] == s["sample_id"] for it in todo)}
    print(f"judge {args.judge}: {len(todo)}/{len(items)} questions, {len(stores)} conversations built", flush=True)
    with cf.ThreadPoolExecutor(args.workers) as pool, path.open("a") as fh:
        futures = {pool.submit(run_one, it, *stores[it["sample_id"]], judge): it for it in todo}
        for f in cf.as_completed(futures):
            try:
                fh.write(json.dumps(f.result()) + "\n")
                fh.flush()
            except Exception as exc:
                print(f"  {futures[f]['id']}: {exc!r}", flush=True)
    report(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
