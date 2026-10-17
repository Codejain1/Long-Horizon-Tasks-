"""End to end: answers written from the self-built engine's ≤500-token context, graded like the protocol's arms.

    PYTHONPATH=src ~/horizon-evals/reader-venv/bin/python evals/memory/e2e_recall.py --out ~/horizon-evals/e2e

The engine (fine-tuned embeddings + learned ranker, trained on the non-test LoCoMo questions) picks turns for each
of the 150 test questions; composer-2.5 (Cursor) answers with LoCoMo's own prompts (two-option prompt for
category 5); Codex grades with the J judge, the same as the protocol's LoCoMo arms. To match those arms'
conditions, Cursor and Codex run with the normal setup (the raw-CLI switch is ignored here). Then J on
categories 1-4 is compared with the protocol arms on the questions every arm answered.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import sys
from math import comb
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import generalize as G  # noqa: E402
import learned_recall as L  # noqa: E402
import locomo  # noqa: E402
import longmemeval as LME  # noqa: E402

LME.raw_env = lambda tool: None  # the protocol's LoCoMo arms ran without the raw switch


def context(conv, picked: list[str], header: str) -> str:
    by_id = {t["id"]: t for t in conv.turns}
    order = {t["id"]: i for i, t in enumerate(conv.turns)}
    lines = [f"DATE: {by_id[i]['date']}\n{by_id[i]['speaker']}: {by_id[i]['text']}"
             for i in sorted(picked, key=order.get)]
    return header + "\n\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tokens", type=int, default=500)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--snippets", action="store_true", help="pack sentence snippets (snippets.pack) instead of whole turns")
    ap.add_argument("--dates", action="store_true", help="resolve relative times at save time (dates.py)")
    ap.add_argument("--protocol", default=str(Path.home() / "horizon-evals" / "mem-eval-v3" / "locomo"))
    args = ap.parse_args(argv)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    name = ("snippets" if args.snippets else "ours") + ("-dates" if args.dates else "")
    path = out / f"{name}.jsonl"
    data = json.loads(locomo.DATA.read_text())
    items = locomo.flatten(data)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data for qa in s["qa"]}
    for it in items:
        it["evidence"] = ev.get(it["id"], [])
    test = locomo.sample_items(list(items), 150)
    test_ids = {it["id"] for it in test}
    train = [it for it in items if it["id"] not in test_ids and it["evidence"]]
    engine = G.Engine(str(Path.home() / "horizon-evals" / "embed-test"))
    import dates as D

    convs = {s["sample_id"]: G.index(D.annotate_conversation(s["conversation"]) if args.dates else s["conversation"],
                                     engine, cache=f"locomo-embed-test{'-dates' if args.dates else ''}-{s['sample_id']}")
             for s in data}
    ranker = G.train_ranker(train, convs, engine)
    import snippets as S

    sidx = {sid: S.SentIndex(c, engine, cache=f"sent-embed-test{'-dates' if args.dates else ''}-{sid}")
            for sid, c in convs.items()} if args.snippets else {}
    done = {json.loads(x)["id"] for x in path.read_text().splitlines()} if path.exists() else set()
    todo = [it for it in test if it["id"] not in done]
    print(f"answering {len(todo)}/{len(test)} questions from ≤{args.tokens}-token contexts", flush=True)

    def one(it):
        conv = convs[it["sample_id"]]
        qv = engine.question(it["question"])
        probs = ranker.predict(L.features(conv, it["question"], qv))
        if args.snippets:
            packed, used = S.pack(conv, sidx[it["sample_id"]], probs, qv, args.tokens)
            order = {t["id"]: i for i, t in enumerate(conv.turns)}
            packed.sort(key=lambda x: x[0])
            picked = [conv.turns[j]["id"] for j, _, _ in packed]
            ctx = it["header"] + "\n\n".join(f"DATE: {conv.turns[j]['date']}\n{conv.turns[j]['speaker']}: {text}"
                                                for j, text, _ in packed)
        else:
            picked, used = L.select(conv, probs, args.tokens)
            ctx = context(conv, picked, it["header"])
        if it["category"] == 5:
            question, options = locomo.cat5_question(it)
            answer, _ = LME.cursor_complete(ctx + locomo.QA_PROMPT_CAT_5.format(question), "composer-2.5")
            answer = locomo.read_option(answer, options)
            correct, _ = locomo.score_locomo(answer, it["answer"], 5)
            j = correct
        else:
            answer, _ = LME.cursor_complete(ctx + locomo.QA_PROMPT.format(it["question"]), "composer-2.5")
            j = locomo.judge_j(it["question"], it["answer"], answer, "default", LME.codex_complete)
        e = G.evidence(it)
        return {"id": it["id"], "category": it["category"], "j": bool(j), "tokens": used,
                "recall": (len(e & set(picked)) / len(e)) if e else None, "answer": answer[-500:]}

    with cf.ThreadPoolExecutor(args.workers) as pool, path.open("a") as fh:
        futures = {pool.submit(one, it): it for it in todo}
        for i, f in enumerate(cf.as_completed(futures), 1):
            try:
                fh.write(json.dumps(f.result()) + "\n")
                fh.flush()
            except Exception as exc:
                print(f"  {futures[f]['id']}: {exc!r}", flush=True)
            if i % 25 == 0:
                print(f"  {i}/{len(todo)} answered", flush=True)

    ours = {r["id"]: r for r in map(json.loads, path.read_text().splitlines())}
    arms = {name + " (≤500 tok)": ours}
    for base, label in (("ours", "whole turns"), ("snippets", "snippets")):
        f = out / f"{base}.jsonl"
        if f != path and f.exists():
            arms[label] = {r["id"]: r for r in map(json.loads, f.read_text().splitlines())}
    for arm in ("horizon", "hybrid", "rag", "full"):
        p = Path(args.protocol) / f"{arm}.jsonl"
        if p.exists():
            arms[arm] = {r["id"]: r for r in map(json.loads, p.read_text().splitlines())}
    ids = [i for i in ours if ours[i]["category"] != 5 and all(i in a for a in arms.values())]
    print(f"\nJ on categories 1-4, {len(ids)} questions every arm answered:")
    for name, a in arms.items():
        print(f"  {name:16} {100 * np.mean([bool(a[i]['j']) for i in ids]):5.1f}%")
    x = [int(ours[i]["j"]) for i in ids]
    for name in ("snippets", "whole turns", "horizon", "full"):
        if name not in arms:
            continue
        y = [int(bool(arms[name][i]["j"])) for i in ids]
        w = sum(1 for p, q in zip(x, y) if p and not q)
        lo = sum(1 for p, q in zip(x, y) if q and not p)
        pv = min(1, 2 * sum(comb(w + lo, k) for k in range(min(w, lo) + 1)) / 2 ** (w + lo)) if w + lo else 1
        random.seed(0)
        n = len(ids)
        g = sorted(sum(x[k] - y[k] for k in [random.randrange(n) for _ in range(n)]) / n * 100 for _ in range(5000))
        print(f"  this - {name}: {100 * (sum(x) - sum(y)) / n:+.1f} pts [{g[125]:+.1f}, {g[4875]:+.1f}]  "
              f"wins {w} losses {lo}  p = {pv:.3f}")
    c5 = [r for r in ours.values() if r["category"] == 5]
    if c5:
        print(f"  category 5 (never mentioned), ours: {sum(r['j'] for r in c5)}/{len(c5)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
