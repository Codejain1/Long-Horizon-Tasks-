"""Generalisation check for the self-built recall engine: does it work on people and data it never trained on?

    ~/horizon-evals/reader-venv/bin/python evals/memory/generalize.py --check locomo-split
    ~/horizon-evals/reader-venv/bin/python evals/memory/generalize.py --check longmemeval --n 150

- locomo-split: fine-tune the embeddings and train the ranker on 7 LoCoMo conversations; test on every question
  of the other 3 (no person, topic or wording shared with training).
- longmemeval: the engine trained on LoCoMo (no retraining) on LongMemEval-S test questions; evidence = turns
  marked `has_answer`. Its turns are long (~95 tokens), so budgets of 500/1000/2000 tokens are reported.
Each compares three engines on the same questions: plain meaning search (stock bge-small), the learned ranker
on stock embeddings, and the learned ranker on fine-tuned embeddings. Question embedding runs on the CPU.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent))
import fast_recall as F  # noqa: E402
import finetune_embed as E  # noqa: E402
import learned_recall as L  # noqa: E402
import locomo  # noqa: E402

TEST_CONVS = ["conv-48", "conv-49", "conv-50"]
CACHE = Path.home() / ".cache" / "horizon-bench" / "generalize_vecs"


def evidence(it) -> set[str]:
    return {x.strip() for x in it["evidence"] for x in str(x).split(";") if x.strip()}


class Engine:
    """One embedding model (stock fastembed or a fine-tuned checkpoint) for turns and questions."""

    def __init__(self, path: str | None):
        # the stock model runs through the same code on the GPU (fastembed's CPU path took hours on LongMemEval)
        self.path = path or E.BASE
        self.tag = "stock" if path is None else Path(path).name
        self.enc = E.Encoder(self.path)
        self.qenc = E.Encoder(self.path)
        self.qenc.model.to("cpu")

    def turns(self, texts: list[str], cache: str | None = None) -> np.ndarray:
        f = CACHE / f"{self.tag}-{cache}.npy" if cache else None
        if f is not None and f.exists():
            return np.load(f)
        with torch.no_grad():
            v = torch.cat([self.enc.encode(texts[i:i + 64]) for i in range(0, len(texts), 64)]).cpu().numpy()
        if f is not None:
            CACHE.mkdir(parents=True, exist_ok=True)
            np.save(f, v)
        return v

    def question(self, text: str) -> np.ndarray:
        with torch.no_grad():
            x = self.qenc.tok([E.QUERY + text], truncation=True, max_length=64, return_tensors="pt")
            return torch.nn.functional.normalize(self.qenc.model(**x).last_hidden_state[:, 0], dim=-1).numpy()[0]


def index(conv_dict: dict, engine: Engine, cache: str | None = None) -> F.Conversation:
    conv = F.Conversation(conv_dict)
    conv.vecs = engine.turns([E.turn_text(t) for t in conv.turns], cache)
    return conv


def train_ranker(items, convs, engine) -> L.MLP:
    X, Y = [], []
    for it in items:
        conv = convs[it["sample_id"]]
        X.append(L.features(conv, it["question"], engine.question(it["question"])))
        e = evidence(it)
        Y.append(np.array([1.0 if t["id"] in e else 0.0 for t in conv.turns], np.float32))
    model = L.MLP(L.N_FEAT, 32)
    model.fit(np.concatenate(X), np.concatenate(Y), epochs=12)
    return model


def finetune(items, convs, out: Path, negs: int = 2, batch: int = 16) -> str:
    rng = np.random.default_rng(1)
    triples = []
    for it in items:
        conv = convs[it["sample_id"]]
        e = evidence(it)
        s = F._norm(conv.bm_turn.scores(F.words(it["question"]))) + F._norm(conv.bm_win.scores(F.words(it["question"])))
        hard = [j for j in np.argsort(-s)[:30] if conv.turns[j]["id"] not in e]
        for t in conv.turns:
            if t["id"] in e:
                pick = rng.choice(hard, size=min(negs, len(hard)), replace=False) if hard else []
                triples.append((it["question"], E.turn_text(t), [E.turn_text(conv.turns[k]) for k in pick]))
    enc = E.Encoder()
    t0 = time.perf_counter()
    enc.fit(triples, epochs=2, batch=batch)
    enc.model.save_pretrained(out)
    enc.tok.save_pretrained(out)
    print(f"  fine-tuned on {len(triples)} triples in {time.perf_counter() - t0:.0f}s -> {out}", flush=True)
    del enc
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    return str(out)


def evaluate(name, items, convs, engine, ranker, budgets):
    rows = {b: [] for b in budgets}
    lat = []
    for it in items:
        conv = convs[it["sample_id"]]
        e = evidence(it)
        if not e:
            continue
        t = time.perf_counter()
        qv = engine.question(it["question"])
        score = ranker.predict(L.features(conv, it["question"], qv)) if ranker else conv.vecs @ qv
        lat.append(time.perf_counter() - t)
        for b in budgets:
            got = set(L.select(conv, score, b)[0])
            rows[b].append(len(e & got) / len(e))
    lat.sort()
    parts = "  ".join(f"{b} tok: {100 * np.mean(rows[b]):5.1f}%" for b in budgets)
    print(f"  {name:38} n={len(rows[budgets[0]])}  recall  {parts}  | p50 {1000 * lat[len(lat) // 2]:.1f} ms "
          f"max {1000 * lat[-1]:.1f} ms", flush=True)


def locomo_split():
    data = json.loads(locomo.DATA.read_text())
    items = locomo.flatten(data)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data for qa in s["qa"]}
    for it in items:
        it["evidence"] = ev.get(it["id"], [])
    train = [it for it in items if it["sample_id"] not in TEST_CONVS and it["evidence"]]
    test = [it for it in items if it["sample_id"] in TEST_CONVS and it["evidence"]]
    print(f"[locomo-split] train: {len(train)} questions from 7 conversations; test: {len(test)} questions from "
          f"{', '.join(TEST_CONVS)} (never seen)", flush=True)
    convs_kw = {s["sample_id"]: F.Conversation(s["conversation"]) for s in data}
    out = finetune(train, convs_kw, Path.home() / "horizon-evals" / "embed-split")
    for label, path, use_ranker in [("plain meaning search (stock)", None, False),
                                    ("learned ranker + stock embeddings", None, True),
                                    ("learned ranker + fine-tuned embeddings", out, True)]:
        engine = Engine(path)
        convs = {s["sample_id"]: index(s["conversation"], engine) for s in data}
        ranker = train_ranker(train, convs, engine) if use_ranker else None
        evaluate(label, test, convs, engine, ranker, [500])


def lme_conv(q: dict) -> tuple[dict, list[str]]:
    conv, ev = {"speaker_a": "user", "speaker_b": "assistant"}, []
    for i, (date, sess) in enumerate(zip(q["haystack_dates"], q["haystack_sessions"]), 1):
        turns = []
        for j, t in enumerate(sess):
            did = f"D{i}:{j}"
            turns.append({"speaker": t["role"], "dia_id": did, "text": t["content"]})
            if t.get("has_answer"):
                ev.append(did)
        conv[f"session_{i}"] = turns
        conv[f"session_{i}_date_time"] = date
    return conv, ev


def longmemeval(n: int):
    sys.path.insert(0, str(Path(__file__).parent))
    import longmemeval as LME

    data = json.loads(LME.DATA.read_text())
    qs = [q for q in LME.split(data, "test") if any(t.get("has_answer") for s in q["haystack_sessions"] for t in s)]
    qs = qs[:n]
    print(f"[longmemeval] {len(qs)} test questions with answer-turn labels; engines trained on LoCoMo only", flush=True)
    data_l = json.loads(locomo.DATA.read_text())
    items_l = locomo.flatten(data_l)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data_l for qa in s["qa"]}
    for it in items_l:
        it["evidence"] = ev.get(it["id"], [])
    test_ids = {it["id"] for it in locomo.sample_items(list(items_l), 150)}
    train_l = [it for it in items_l if it["id"] not in test_ids and it["evidence"]]
    for label, path, use_ranker in [("plain meaning search (stock)", None, False),
                                    ("learned ranker + stock embeddings", None, True),
                                    ("learned ranker + fine-tuned embeddings",
                                     str(Path.home() / "horizon-evals" / "embed-test"), True)]:
        engine = Engine(path)
        ranker = None
        if use_ranker:
            convs_l = {s["sample_id"]: index(s["conversation"], engine) for s in data_l}
            ranker = train_ranker(train_l, convs_l, engine)
        items, convs = [], {}
        for q in qs:
            conv, evid = lme_conv(q)
            convs[q["question_id"]] = index(conv, engine, cache=f"lme-{q['question_id']}")
            items.append({"sample_id": q["question_id"], "question": q["question"], "evidence": evid})
        evaluate(label, items, convs, engine, ranker, [500, 1000, 2000])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", choices=["locomo-split", "longmemeval"], required=True)
    ap.add_argument("--n", type=int, default=150)
    args = ap.parse_args(argv)
    locomo_split() if args.check == "locomo-split" else longmemeval(args.n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
