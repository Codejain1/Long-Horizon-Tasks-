"""Snippets: send the best sentence(s) of each relevant turn instead of whole turns, within the same budget.

    PYTHONPATH=src ~/horizon-evals/reader-venv/bin/python evals/memory/snippets.py

Held-out LoCoMo questions only (dev split; ranker and embeddings trained on the fit questions). At index time each
turn is split into sentences and every sentence embedded once (cached). At question time turns are ranked as
before; for the top turns each sentence is scored (turn relevance × sentence-question similarity) and kept by
rules: short turns whole; otherwise the best sentence, plus the one before it when it leans on it (pronoun start),
plus the runner-up when it is close; the top 2 turns always whole. Packing: by relevance until the budget is full.

Compared with whole-turn selection at the same budget:
- evidence turns covered (any part of the turn sent),
- answer words present: share of the gold answer's content words found in what is sent (did the part that
  matters survive?),
- distinct turns covered.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import fast_recall as F  # noqa: E402
import generalize as G  # noqa: E402
import learned_recall as L  # noqa: E402
import locomo  # noqa: E402

_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")
_LEANS = re.compile(r"^(she|he|it|they|that|this|there|those|these|then|so|and|but|which)\b", re.I)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text) if s.strip()]


def answer_words(gold: str) -> set[str]:
    return set(F.words(gold))


class SentIndex:
    """Sentence vectors per turn, from the same embedding engine, cached on disk."""

    def __init__(self, conv, engine, cache: str):
        self.sents = [sentences(t["text"]) or [t["text"]] for t in conv.turns]
        flat = [f"{t['speaker']}: {s}" for t, ss in zip(conv.turns, self.sents) for s in ss]
        vecs = engine.turns(flat, cache)
        self.vecs, k = [], 0
        for ss in self.sents:
            self.vecs.append(vecs[k:k + len(ss)])
            k += len(ss)


def pack(conv, si: SentIndex, probs, qvec, budget: int, whole_top: int = 2, close: float = 0.05, short: int = 25):
    order = np.argsort(-probs)
    out, used = [], 0  # (turn index, text, whole?)
    for rank, j in enumerate(order[:60]):
        t = conv.turns[j]
        head = f"[{t['date']}] {t['speaker']}: "
        tok = L.tokens(t["text"])
        if rank < whole_top or tok <= short or len(si.sents[j]) == 1:
            text, whole = t["text"], True
        else:
            sims = si.vecs[j] @ qvec
            best = int(np.argmax(sims))
            keep = {best}
            second = np.argsort(-sims)[1] if len(sims) > 1 else None
            if second is not None and sims[best] - sims[second] < close:
                keep.add(int(second))
            if best > 0 and _LEANS.match(si.sents[j][best]):
                keep.add(best - 1)
            text, whole = " … ".join(si.sents[j][k] for k in sorted(keep)), False
        cost = L.tokens(head + text)
        if used + cost <= budget:
            out.append((int(j), text, whole))
            used += cost
        if used >= budget - 5:
            break
    return out, used


def select_whole(conv, probs, budget):
    """Whole turns, charged for the same "[date] speaker:" line the snippets pay for (a fair comparison)."""
    out, used = [], 0
    for j in np.argsort(-probs):
        t = conv.turns[j]
        cost = L.tokens(f"[{t['date']}] {t['speaker']}: {t['text']}")
        if used + cost <= budget:
            out.append(t["id"])
            used += cost
        if used >= budget - 5:
            break
    return out, used


def lme(n: int) -> int:
    import longmemeval as LME

    data = json.loads(LME.DATA.read_text())
    qs = [q for q in LME.split(data, "test") if any(t.get("has_answer") for ss in q["haystack_sessions"] for t in ss)][:n]
    data_l = json.loads(locomo.DATA.read_text())
    items_l = locomo.flatten(data_l)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data_l for qa in s["qa"]}
    for it in items_l:
        it["evidence"] = ev.get(it["id"], [])
    test_ids = {it["id"] for it in locomo.sample_items(list(items_l), 150)}
    train_l = [it for it in items_l if it["id"] not in test_ids and it["evidence"]]
    engine = G.Engine(str(Path.home() / "horizon-evals" / "embed-test"))
    convs_l = {s["sample_id"]: G.index(s["conversation"], engine, cache=f"locomo-embed-test-{s['sample_id']}") for s in data_l}
    ranker = G.train_ranker(train_l, convs_l, engine)
    print(f"[longmemeval snippets] {len(qs)} test questions; ranker + fine-tuned embeddings trained on LoCoMo only",
          flush=True)
    res = {b: {"whole": [], "snip": []} for b in (500, 1000)}
    for q in qs:
        conv_d, evid = G.lme_conv(q)
        conv = G.index(conv_d, engine, cache=f"lme-{q['question_id']}")
        si = SentIndex(conv, engine, cache=f"sent-lme-{q['question_id']}")
        e = set(evid)
        qv = engine.question(q["question"])
        probs = ranker.predict(L.features(conv, q["question"], qv))
        for b in (500, 1000):
            wid, _ = select_whole(conv, probs, b)
            packed, _ = pack(conv, si, probs, qv, b)
            res[b]["whole"].append((len(e & set(wid)) / len(e), len(wid)))
            res[b]["snip"].append((len(e & {conv.turns[j]["id"] for j, _, _ in packed}) / len(e), len(packed)))
    for b in (500, 1000):
        for name, label in (("whole", "whole turns"), ("snip", "snippets   ")):
            r = res[b][name]
            print(f"  {b} tokens  {label}  evidence turns covered {100 * np.mean([x[0] for x in r]):5.1f}%  "
                  f"turns sent {np.mean([x[1] for x in r]):5.1f}", flush=True)
    return 0


def main() -> int:
    if "--lme" in sys.argv:
        return lme(int(sys.argv[sys.argv.index("--lme") + 1]))
    data = json.loads(locomo.DATA.read_text())
    items = locomo.flatten(data)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data for qa in s["qa"]}
    for it in items:
        it["evidence"] = ev.get(it["id"], [])
    test_ids = {it["id"] for it in locomo.sample_items(list(items), 150)}
    train = [it for it in items if it["id"] not in test_ids and it["evidence"]]
    idx = np.random.default_rng(3).permutation(len(train))
    cut = int(0.8 * len(train))
    fit, dev = [train[i] for i in idx[:cut]], [train[i] for i in idx[cut:]]
    engine = G.Engine(str(Path.home() / "horizon-evals" / "embed-dev"))
    convs = {s["sample_id"]: G.index(s["conversation"], engine, cache=f"locomo-embed-dev-{s['sample_id']}") for s in data}
    sidx = {sid: SentIndex(c, engine, cache=f"sent-embed-dev-{sid}") for sid, c in convs.items()}
    ranker = G.train_ranker(fit, convs, engine)
    if "--tune" in sys.argv:  # rule grid on the held-out questions (evidence covered at 500 tokens)
        cache = {}
        for it in dev:
            if it["category"] != 5:
                conv = convs[it["sample_id"]]
                qv = engine.question(it["question"])
                cache[it["id"]] = (qv, ranker.predict(L.features(conv, it["question"], qv)))
        for short in (15, 25, 40):
            for whole_top in (0, 1, 2):
                for close in (0.05, 0.1):
                    r = []
                    for it in dev:
                        if it["category"] == 5:
                            continue
                        conv, si = convs[it["sample_id"]], sidx[it["sample_id"]]
                        qv, probs = cache[it["id"]]
                        e = G.evidence(it)
                        packed, _ = pack(conv, si, probs, qv, 500, whole_top, close, short)
                        r.append(len(e & {conv.turns[j]["id"] for j, _, _ in packed}) / len(e))
                    print(f"  short≤{short:2} whole_top={whole_top} close={close}: evidence covered {100 * np.mean(r):5.1f}%")
        return 0
    for budget in (500, 1000):
        res = {"whole": [], "snip": []}
        for it in dev:
            if it["category"] == 5:
                continue
            conv, si = convs[it["sample_id"]], sidx[it["sample_id"]]
            e = G.evidence(it)
            qv = engine.question(it["question"])
            probs = ranker.predict(L.features(conv, it["question"], qv))
            aw = answer_words(it["answer"])
            whole_ids, _ = select_whole(conv, probs, budget)
            whole_text = " ".join(t["text"] for t in conv.turns if t["id"] in set(whole_ids))
            packed, _ = pack(conv, si, probs, qv, budget)
            snip_ids = {conv.turns[j]["id"] for j, _, _ in packed}
            snip_text = " ".join(text for _, text, _ in packed)
            for name, ids, text in (("whole", set(whole_ids), whole_text), ("snip", snip_ids, snip_text)):
                res[name].append((len(e & ids) / len(e), len(aw & set(F.words(text))) / len(aw) if aw else None,
                                  len(ids)))
        print(f"budget {budget} tokens, {len(res['whole'])} held-out questions (categories 1-4):")
        for name, label in (("whole", "whole turns"), ("snip", "snippets   ")):
            r = res[name]
            aw = [x[1] for x in r if x[1] is not None]
            print(f"  {label}  evidence turns covered {100 * np.mean([x[0] for x in r]):5.1f}%  "
                  f"answer words present {100 * np.mean(aw):5.1f}%  turns sent {np.mean([x[2] for x in r]):5.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
