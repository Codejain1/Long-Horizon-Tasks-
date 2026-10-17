"""Combo recall: fine-tuned embeddings + learned ranker (stage 1), then the fine-tuned reader on the top K (stage 2).

    ~/horizon-evals/reader-venv/bin/python evals/memory/combo_recall.py --mode dev --embed ~/horizon-evals/embed-dev \
        --reader ~/horizon-evals/reader-dev-model --k 4 6 8 --qdevice cpu mps

Loads the saved models (no training except the ranker, seconds), re-embeds turns once, then times every query
end to end: question embedding, stage 1 over every turn, stage 2 over K candidates, budget fill. Reports
recall and p50/p95/max latency per (K, question-embedding device). Tune on --mode dev; run --mode test once.
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
import reader_recall as R  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dev", "test"], default="dev")
    ap.add_argument("--embed", required=True)
    ap.add_argument("--reader", required=True)
    ap.add_argument("--k", type=int, nargs="+", default=[6])
    ap.add_argument("--qdevice", nargs="+", default=["cpu"], help="device for the question embedding")
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--tokens", type=int, default=500)
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

    def evidence(it):
        return {x.strip() for x in it["evidence"] for x in str(x).split(";") if x.strip()}

    convs = {s["sample_id"]: F.Conversation(s["conversation"]) for s in data}
    enc = E.Encoder(str(Path(args.embed).expanduser()))
    with torch.no_grad():
        for conv in convs.values():
            texts = [E.turn_text(t) for t in conv.turns]
            conv.vecs = torch.cat([enc.encode(texts[i:i + 128]) for i in range(0, len(texts), 128)]).cpu().numpy()
    encoders = {}
    for dev in args.qdevice:  # a copy of the encoder per device, for the question at query time
        e = E.Encoder(str(Path(args.embed).expanduser()))
        e.model.to(dev)
        encoders[dev] = e

    def qv(text, dev):
        e = encoders[dev]
        with torch.no_grad():
            x = e.tok([E.QUERY + text], truncation=True, max_length=64, return_tensors="pt").to(dev)
            v = torch.nn.functional.normalize(e.model(**x).last_hidden_state[:, 0], dim=-1)
        return v.cpu().numpy()[0]

    X, Y = [], []
    for it in fit:
        conv = convs[it["sample_id"]]
        X.append(L.features(conv, it["question"], qv(it["question"], args.qdevice[0])))
        e_ = evidence(it)
        Y.append(np.array([1.0 if t["id"] in e_ else 0.0 for t in conv.turns], np.float32))
    ranker = L.MLP(L.N_FEAT, 32)
    ranker.fit(np.concatenate(X), np.concatenate(Y), epochs=12)
    reader = R.Reader(str(Path(args.reader).expanduser()))

    def run(k, dev):
        rows = []
        with torch.no_grad():
            for w in range(3):  # warm-up: first calls compile kernels; not queries
                qv("warm up", dev)
                reader.logits("warm up", ["warm up"] * max(k, 1), max_len=args.max_len)
            for it in evaluate:
                conv = convs[it["sample_id"]]
                e_ = evidence(it)
                t = time.perf_counter()
                p = ranker.predict(L.features(conv, it["question"], qv(it["question"], dev)))
                if k:
                    top = np.argsort(-p)[:k]
                    read = torch.sigmoid(reader.logits(it["question"], [R.passage(conv, j) for j in top],
                                                       max_len=args.max_len)).cpu().numpy()
                    final = p * 0.5  # turns the reader didn't see keep their stage-1 order, below the read ones
                    final[top] = 1.0 + read
                else:
                    final = p
                got, used = L.select(conv, final, args.tokens)
                secs = time.perf_counter() - t
                got = set(got)
                rows.append((len(e_ & got) / len(e_), e_ <= got, secs, it["category"]))
        lat = sorted(r[2] for r in rows)
        print(f"[{args.mode}] K={k:2} qdevice={dev:3} recall {100 * np.mean([r[0] for r in rows]):5.1f}%  "
              f"all {100 * np.mean([r[1] for r in rows]):5.1f}%  p50 {1000 * lat[len(lat) // 2]:6.1f} ms  "
              f"p95 {1000 * lat[int(.95 * len(lat))]:6.1f} ms  max {1000 * lat[-1]:6.1f} ms", flush=True)
        return rows

    for dev in args.qdevice:
        for k in args.k:
            rows = run(k, dev)
    for cat in sorted({r[3] for r in rows}):
        sub = [r for r in rows if r[3] == cat]
        print(f"  (last config) category {cat}: recall {100 * np.mean([r[0] for r in sub]):5.1f}%  n={len(sub)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
