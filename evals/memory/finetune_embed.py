"""Fine-tune the embedding model on LoCoMo so the ranker's semantic signal learns what "relevant" means here.

    ~/horizon-evals/reader-venv/bin/python evals/memory/finetune_embed.py --mode dev    # fit 80 %, eval 20 %
    ~/horizon-evals/reader-venv/bin/python evals/memory/finetune_embed.py --mode test   # all non-test, eval the 150

The reader showed that reading deeper finds more evidence (dev 86.5 %) but costs ~200 ms per query. This moves
the learning to index time instead: bge-small is fine-tuned contrastively (question vs the turns that answer it,
against in-batch and hard negatives the ranker confused), turns are re-embedded once, and the numpy ranker is
retrained on the new similarities. A query stays one small embedding plus the ranker.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as Fn

sys.path.insert(0, str(Path(__file__).parent))
import fast_recall as F  # noqa: E402
import learned_recall as L  # noqa: E402
import locomo  # noqa: E402

BASE = "BAAI/bge-small-en-v1.5"
QUERY = "Represent this sentence for searching relevant passages: "  # bge's own query instruction
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


class Encoder:
    def __init__(self, path: str = BASE):
        from transformers import AutoModel, AutoTokenizer

        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = AutoModel.from_pretrained(path).to(DEVICE)

    def encode(self, texts: list[str], grad: bool = False, max_len: int = 96) -> torch.Tensor:
        enc = self.tok(texts, truncation=True, max_length=max_len, padding=True, return_tensors="pt").to(DEVICE)
        with torch.set_grad_enabled(grad):
            cls = self.model(**enc).last_hidden_state[:, 0]  # bge pools with the CLS token
        return Fn.normalize(cls, dim=-1)

    def fit(self, triples, epochs: int = 2, batch: int = 32, lr: float = 2e-5, temp: float = 0.05):
        """InfoNCE: each question against its positive, every other passage in the batch and its hard negatives."""
        self.model.train()
        opt = torch.optim.AdamW(self.model.parameters(), lr=lr)
        rng = np.random.default_rng(0)
        for _ in range(epochs):
            order = rng.permutation(len(triples))
            for s in range(0, len(triples), batch):
                b = [triples[i] for i in order[s:s + batch]]
                q = self.encode([QUERY + x[0] for x in b], grad=True)
                pos = [x[1] for x in b]
                negs = [n for x in b for n in x[2]]
                p = self.encode(pos + negs, grad=True)
                logits = q @ p.T / temp
                loss = Fn.cross_entropy(logits, torch.arange(len(b), device=DEVICE))
                opt.zero_grad()
                loss.backward()
                opt.step()
        self.model.eval()


def turn_text(t: dict) -> str:
    return f"{t['speaker']}: {t['text']}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dev", "test"], default="dev")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--negs", type=int, default=2, help="hard negatives per question")
    ap.add_argument("--tokens", type=int, default=500)
    ap.add_argument("--batch", type=int, default=16, help="questions per step (8 GB Macs: 16 with 2 negatives)")
    ap.add_argument("--base", default=BASE, help="starting model, e.g. BAAI/bge-base-en-v1.5")
    ap.add_argument("--neg-model", default=None, help="mine hard negatives by this (fine-tuned) model's similarity")
    ap.add_argument("--out", default=None, help="where to save the fine-tuned model")
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
    convs = {s["sample_id"]: F.Conversation(s["conversation"]) for s in data}  # keyword index; vectors below

    def evidence(it):
        return {x.strip() for x in it["evidence"] for x in str(x).split(";") if x.strip()}

    # 1. Training triples: question, an evidence turn, hard negatives: turns the keyword index ranks high, or
    # (round 2, --neg-model) turns an already fine-tuned model confuses with the answer.
    miner = None
    if args.neg_model:
        miner = Encoder(str(Path(args.neg_model).expanduser()))
        with torch.no_grad():
            for conv in convs.values():
                texts = [turn_text(t) for t in conv.turns]
                conv.mined = torch.cat([miner.encode(texts[i:i + 128]) for i in range(0, len(texts), 128)]).cpu().numpy()
    rng = np.random.default_rng(1)
    triples = []
    for it in fit:
        conv = convs[it["sample_id"]]
        e = evidence(it)
        s = F._norm(conv.bm_turn.scores(F.words(it["question"]))) + F._norm(conv.bm_win.scores(F.words(it["question"])))
        if miner is not None:
            with torch.no_grad():
                s = conv.mined @ miner.encode([QUERY + it["question"]]).cpu().numpy()[0]
        hard = [j for j in np.argsort(-s)[:30] if conv.turns[j]["id"] not in e]
        for j, t in enumerate(conv.turns):
            if t["id"] in e:
                pick = rng.choice(hard, size=min(args.negs, len(hard)), replace=False) if hard else []
                triples.append((it["question"], turn_text(t), [turn_text(conv.turns[k]) for k in pick]))
    t0 = time.perf_counter()
    if miner is not None:
        del miner
        for conv in convs.values():
            del conv.mined
    enc = Encoder(args.base)
    enc.fit(triples, epochs=args.epochs, batch=args.batch)
    out_dir = Path(args.out).expanduser() if args.out else Path.home() / "horizon-evals" / f"embed-{args.mode}"
    enc.model.save_pretrained(out_dir)
    enc.tok.save_pretrained(out_dir)
    print(f"[{args.mode}] fine-tuned on {len(triples)} triples in {time.perf_counter() - t0:.0f}s on {DEVICE}", flush=True)

    # 2. Re-embed every turn once (index time).
    with torch.no_grad():
        for conv in convs.values():
            texts = [turn_text(t) for t in conv.turns]
            conv.vecs = torch.cat([enc.encode(texts[i:i + 128]) for i in range(0, len(texts), 128)]).cpu().numpy()

    qenc = Encoder(str(out_dir))  # the question is embedded on the CPU: the GPU's first-call spikes broke the max
    qenc.model.to("cpu")

    def qv(text):
        with torch.no_grad():
            x = qenc.tok([QUERY + text], truncation=True, max_length=64, return_tensors="pt")
            return Fn.normalize(qenc.model(**x).last_hidden_state[:, 0], dim=-1).numpy()[0]

    # 3. Retrain the ranker on the new similarities, then evaluate.
    X, Y = [], []
    for it in fit:
        conv = convs[it["sample_id"]]
        X.append(L.features(conv, it["question"], qv(it["question"])))
        e = evidence(it)
        Y.append(np.array([1.0 if t["id"] in e else 0.0 for t in conv.turns], np.float32))
    ranker = L.MLP(L.N_FEAT, 32)
    ranker.fit(np.concatenate(X), np.concatenate(Y), epochs=12)
    qv("warm up")
    rows = []
    for it in evaluate:
        conv = convs[it["sample_id"]]
        e = evidence(it)
        t = time.perf_counter()  # whole query: embed the question, score every turn, fill the budget
        got, used = L.select(conv, ranker.predict(L.features(conv, it["question"], qv(it["question"]))), args.tokens)
        secs = time.perf_counter() - t
        got = set(got)
        rows.append({"cat": it["category"], "recall": len(e & got) / len(e), "all": e <= got, "secs": secs,
                     "tokens": used})
    lat = sorted(r["secs"] for r in rows)
    print(f"[{args.mode}] fine-tuned embeddings + ranker, budget {args.tokens}: n={len(rows)} "
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
