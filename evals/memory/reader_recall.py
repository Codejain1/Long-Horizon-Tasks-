"""Reader recall: the learned ranker's top candidates re-read by a cross-encoder fine-tuned here on LoCoMo.

    ~/horizon-evals/reader-venv/bin/python evals/memory/reader_recall.py --mode dev    # train on 80 %, eval 20 %
    ~/horizon-evals/reader-venv/bin/python evals/memory/reader_recall.py --mode test   # train on all non-test, eval the 150

Stage 1 (learned_recall.py): every turn scored from keyword, semantic and structural features by a numpy MLP.
Stage 2 (this file): the top K turns are read by a small cross-encoder (MiniLM-L6, open weights as the
starting point) fine-tuned on the non-test questions: "question" against "previous turn || turn". The budget is
filled by the reader's probability. Needs PyTorch (a separate environment); runs on the Mac's GPU (MPS) if present.
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
import learned_recall as L  # noqa: E402
import locomo  # noqa: E402

BASE = "cross-encoder/ms-marco-MiniLM-L-6-v2"
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def passage(conv, j: int) -> str:
    t = conv.turns[j]
    prev = conv.turns[j - 1] if j > 0 and conv.session[j - 1] == conv.session[j] else None
    lead = f"{prev['speaker']}: {prev['text'][:300]} || " if prev else ""
    return f"[{t['date']}] {lead}{t['speaker']}: {t['text']}"


class Reader:
    def __init__(self, path: str = BASE):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path, num_labels=1).to(DEVICE)

    def logits(self, question: str, passages: list[str], max_len: int = 192) -> torch.Tensor:
        enc = self.tok([question] * len(passages), passages, truncation=True, max_length=max_len,
                       padding=True, return_tensors="pt").to(DEVICE)
        return self.model(**enc).logits.squeeze(-1)

    def fit(self, pairs: list[tuple[str, str, float]], epochs: int = 2, batch: int = 32, lr: float = 2e-5):
        self.model.train()
        opt = torch.optim.AdamW(self.model.parameters(), lr=lr)
        pos = sum(p[2] for p in pairs)
        loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor((len(pairs) - pos) / max(pos, 1), device=DEVICE))
        rng = np.random.default_rng(0)
        for _ in range(epochs):
            order = rng.permutation(len(pairs))
            for s in range(0, len(pairs), batch):
                b = [pairs[i] for i in order[s:s + batch]]
                enc = self.tok([x[0] for x in b], [x[1] for x in b], truncation=True, max_length=192,
                               padding=True, return_tensors="pt").to(DEVICE)
                y = torch.tensor([x[2] for x in b], dtype=torch.float32, device=DEVICE)
                loss = loss_fn(self.model(**enc).logits.squeeze(-1), y)
                opt.zero_grad()
                loss.backward()
                opt.step()
        self.model.eval()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dev", "test"], default="dev")
    ap.add_argument("--k", type=int, default=32, help="candidates the reader re-reads")
    ap.add_argument("--tokens", type=int, default=500)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--max-len", type=int, default=192)
    ap.add_argument("--skip-above", type=float, default=1.1, help="skip the reader when stage 1's top prob ≥ this")
    ap.add_argument("--load", default=None, help="a saved reader (skips reader training)")
    ap.add_argument("--save", default=None)
    args = ap.parse_args(argv)
    data = json.loads(locomo.DATA.read_text())
    items = locomo.flatten(data)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data for qa in s["qa"]}
    for it in items:
        it["evidence"] = ev.get(it["id"], [])
    test = locomo.sample_items(list(items), 150)
    test_ids = {it["id"] for it in test}
    train = [it for it in items if it["id"] not in test_ids and it["evidence"]]
    if args.mode == "dev":
        idx = np.random.default_rng(3).permutation(len(train))
        cut = int(0.8 * len(train))
        fit, evaluate = [train[i] for i in idx[:cut]], [train[i] for i in idx[cut:]]
    else:
        fit, evaluate = train, [it for it in test if it["evidence"]]
    from fastembed import TextEmbedding

    emb = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    convs = {s["sample_id"]: F.Conversation(s["conversation"], emb) for s in data}
    qcache: dict[str, np.ndarray] = {}

    def qv(text, cache=True):
        if cache and text in qcache:
            return qcache[text]
        v = np.asarray(next(iter(emb.embed([text]))), dtype=np.float32)
        v = v / np.linalg.norm(v)
        if cache:
            qcache[text] = v
        return v

    def evidence(it):
        return {x.strip() for x in it["evidence"] for x in str(x).split(";") if x.strip()}

    # Stage 1: the learned ranker, trained on the fit questions.
    t0 = time.perf_counter()
    X, Y = [], []
    for it in fit:
        conv = convs[it["sample_id"]]
        X.append(L.features(conv, it["question"], qv(it["question"])))
        e = evidence(it)
        Y.append(np.array([1.0 if t["id"] in e else 0.0 for t in conv.turns], np.float32))
    ranker = L.MLP(L.N_FEAT, 32)
    ranker.fit(np.concatenate(X), np.concatenate(Y), epochs=12)

    # Stage 2: reader trained on each fit question's top-K candidates (plus any evidence the ranker missed).
    pairs = []
    for it, x in zip(fit, X):
        conv = convs[it["sample_id"]]
        e = evidence(it)
        top = list(np.argsort(-ranker.predict(x))[: args.k])
        top += [j for j, t in enumerate(conv.turns) if t["id"] in e and j not in top]
        pairs += [(it["question"], passage(conv, j), 1.0 if conv.turns[j]["id"] in e else 0.0) for j in top]
    reader = Reader(args.load) if args.load else Reader()
    if not args.load:
        reader.fit(pairs, epochs=args.epochs)
    if args.save:
        reader.model.save_pretrained(args.save)
        reader.tok.save_pretrained(args.save)
    print(f"[{args.mode}] trained ranker + reader on {len(fit)} questions ({len(pairs)} pairs) in "
          f"{time.perf_counter() - t0:.0f}s on {DEVICE}", flush=True)

    rows = []
    with torch.no_grad():
        reader.logits("warm up", ["warm up"])  # first GPU call compiles kernels; not a query
        for it in evaluate:
            conv = convs[it["sample_id"]]
            e = evidence(it)
            t = time.perf_counter()  # whole query: embed, stage 1 over every turn, stage 2 over top K, fill budget
            probs = ranker.predict(L.features(conv, it["question"], qv(it["question"], cache=False)))
            top = np.argsort(-probs)[: args.k]
            if probs[top[0]] >= args.skip_above:  # stage 1 is confident: no need to read
                final = probs
            else:
                read = torch.sigmoid(reader.logits(it["question"], [passage(conv, j) for j in top],
                                                   max_len=args.max_len)).cpu().numpy()
                final = np.full(len(conv.turns), -1.0)
                final[top] = read
            got, used = L.select(conv, final, args.tokens)
            secs = time.perf_counter() - t
            got = set(got)
            rows.append({"cat": it["category"], "recall": len(e & got) / len(e), "all": e <= got, "secs": secs,
                         "tokens": used})
    lat = sorted(r["secs"] for r in rows)
    print(f"[{args.mode}] reader recall, K={args.k}, budget {args.tokens}: n={len(rows)} "
          f"evidence recall {100 * sum(r['recall'] for r in rows) / len(rows):.1f}%  "
          f"all found {100 * sum(r['all'] for r in rows) / len(rows):.1f}%  "
          f"tokens {sum(r['tokens'] for r in rows) / len(rows):.0f}  "
          f"latency p50 {1000 * lat[len(lat) // 2]:.1f} ms p95 {1000 * lat[int(len(lat) * .95)]:.1f} ms "
          f"max {1000 * lat[-1]:.1f} ms")
    for cat in sorted({r["cat"] for r in rows}):
        sub = [r for r in rows if r["cat"] == cat]
        print(f"  category {cat}: recall {100 * sum(r['recall'] for r in sub) / len(sub):5.1f}%  n={len(sub)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
