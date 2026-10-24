"""Learned recall: a self-built ranker over keyword + semantic + structural signals, under a token budget.

    python memrouter/learned_recall.py --tokens 500

Every turn of a conversation gets a feature vector (BM25 on the turn and its window, query expansion, the
question-turn embedding similarity from local bge-small, the neighbours' scores, speaker and time cues, the
turn's session strength, its length). A small MLP written in numpy learns, from the LoCoMo questions outside
the 150-question test sample, the probability that a turn holds evidence. A query scores every turn, then
fills the token budget with the most likely turns (best probability per token first). Nothing external:
no API, no judge model; the only pretrained part is the local embedding model.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import fast_recall as F  # noqa: E402

N_FEAT = 22
_NAME = __import__('re').compile(r"\b[A-Z][a-z]{2,}\b")


def tokens(text: str) -> int:
    return max(1, len(text) // 4)


def features(conv: F.Conversation, question: str, qvec: np.ndarray) -> np.ndarray:
    q = F.words(question)
    n = len(conv.turns)
    bt = F._norm(conv.bm_turn.scores(q))
    bw = F._norm(conv.bm_win.scores(q))
    vs = conv.vecs @ qvec
    vn = (vs - vs.mean()) / (vs.std() + 1e-6)
    top = np.argsort(-(bt + bw + F._norm(np.maximum(vs, 0))))[:3]
    extra = [w for w, _ in Counter(w for j in top for w in conv.words[j] if w not in q).most_common(10)]
    pr = F._norm(conv.bm_turn.scores(extra))
    prev = lambda x: np.concatenate([[0.0], x[:-1]])  # noqa: E731
    nxt = lambda x: np.concatenate([x[1:], [0.0]])  # noqa: E731
    sess = np.array(conv.session)
    smax = np.full(sess.max() + 1, -np.inf)  # best turn per session, vectorised (an inbox has tens of thousands)
    np.maximum.at(smax, sess, vn)
    smax = smax[sess].astype(np.float32)
    low = question.lower()
    asked = [full for name, full in conv.names.items() if name in low]
    spk = np.array([1.0 if len(asked) == 1 and t["speaker"] == asked[0] else 0.0 for t in conv.turns])
    other = np.array([1.0 if len(asked) == 1 and t["speaker"] != asked[0] else 0.0 for t in conv.turns])
    when = 1.0 if F._WHEN.search(question) else 0.0
    length = np.log1p([tokens(t["text"]) for t in conv.turns])
    rank_v = np.empty(n)
    rank_v[np.argsort(-vs)] = np.arange(n)
    first = bt + bw + F._norm(np.maximum(vs, 0))
    top5 = np.argsort(-first)[:5]
    # second hop: names and other words the first results add, searched again
    asked_words = set(q) | {w.lower() for w in _NAME.findall(question)}
    names = Counter(w for j in top5 for w in _NAME.findall(conv.turns[j]["text"]) if w.lower() not in asked_words)
    hop = F._norm(conv.bm_turn.scores([F.stem(w.lower()) for w, _ in names.most_common(6)]))
    # semantic expansion (Rocchio): the question blended with its top results
    roc = qvec + conv.vecs[top5[:3]].mean(0)
    roc_s = conv.vecs @ (roc / (np.linalg.norm(roc) + 1e-6))
    roc_n = (roc_s - roc_s.mean()) / (roc_s.std() + 1e-6)
    # session evidence: strong first-stage hits sharing the turn's session
    top50 = np.argsort(-first)[:50]
    hits = Counter(conv.session[j] for j in top50)
    sess_hits = np.array([hits.get(si, 0) for si in conv.session], dtype=np.float32) / 50.0
    return np.stack([bt, bw, pr, vs, vn, prev(vn), nxt(vn), prev(bt), nxt(bt), smax, spk, other,
                     conv.timey * when, conv.timey, length, 1.0 / (1.0 + rank_v), bt * vn,
                     hop, roc_n, sess_hits, hop * roc_n, np.ones(n)],
                    axis=1).astype(np.float32)


class MLP:
    """One hidden layer, trained with Adam on weighted logistic loss. Written here; numpy only."""

    def __init__(self, d: int, h: int = 32, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.p = {"W1": rng.normal(0, 1 / np.sqrt(d), (d, h)).astype(np.float32), "b1": np.zeros(h, np.float32),
                  "W2": rng.normal(0, 1 / np.sqrt(h), (h, 1)).astype(np.float32), "b2": np.zeros(1, np.float32)}
        self.mu = np.zeros(d, np.float32)
        self.sd = np.ones(d, np.float32)

    def forward(self, X):
        Z = (X - self.mu) / self.sd
        H = np.maximum(0, Z @ self.p["W1"] + self.p["b1"])
        return Z, H, (H @ self.p["W2"] + self.p["b2"]).ravel()

    def predict(self, X):
        return 1 / (1 + np.exp(-self.forward(X)[2]))

    def fit(self, X, y, epochs=12, lr=3e-3, batch=4096, pos_weight=None):
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        self.mu[-1], self.sd[-1] = 0.0, 1.0  # the bias feature stays 1
        w_pos = pos_weight or (len(y) - y.sum()) / max(y.sum(), 1)
        m = {k: np.zeros_like(v) for k, v in self.p.items()}
        v2 = {k: np.zeros_like(v) for k, v in self.p.items()}
        rng, t = np.random.default_rng(1), 0
        for _ in range(epochs):
            order = rng.permutation(len(y))
            for s in range(0, len(y), batch):
                idx = order[s:s + batch]
                Z, H, logit = self.forward(X[idx])
                prob = 1 / (1 + np.exp(-logit))
                w = np.where(y[idx] > 0, w_pos, 1.0)
                g = (w * (prob - y[idx]) / w.sum())[:, None]
                grads = {"W2": H.T @ g, "b2": g.sum(0)}
                dH = (g @ self.p["W2"].T) * (H > 0)
                grads["W1"], grads["b1"] = Z.T @ dH, dH.sum(0)
                t += 1
                for k in self.p:
                    m[k] = 0.9 * m[k] + 0.1 * grads[k]
                    v2[k] = 0.999 * v2[k] + 0.001 * grads[k] ** 2
                    self.p[k] -= lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v2[k] / (1 - 0.999 ** t)) + 1e-8)


def select(conv, probs, budget, diverse: float = 0.0):
    order = np.argsort(-probs)  # most likely first ("short first" lost 14 points on held-out questions)
    out, used, chosen = [], 0, []
    for j in order:
        if diverse and chosen and conv.vecs is not None and float((conv.vecs[chosen] @ conv.vecs[j]).max()) > diverse:
            continue  # a near-duplicate of a turn already chosen spends tokens for nothing new
        c = tokens(conv.turns[j]["text"])
        if used + c <= budget:  # a turn that doesn't fit is skipped; a shorter one later may still fit
            out.append(conv.turns[j]["id"])
            chosen.append(j)
            used += c
        if used >= budget - 5:
            break
    return out, used


def main(argv=None) -> int:
    import locomo_data as locomo  # benchmark data: only the CLI needs it, so the engine deploys without it

    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=int, default=500)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--epochs", type=int, default=12)
    args = ap.parse_args(argv)
    data = json.loads(locomo.DATA.read_text())
    items = locomo.flatten(data)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data for qa in s["qa"]}
    for it in items:
        it["evidence"] = ev.get(it["id"], [])
    test = locomo.sample_items(list(items), 150)
    test_ids = {it["id"] for it in test}
    train = [it for it in items if it["id"] not in test_ids and it["evidence"]]
    from fastembed import TextEmbedding

    emb = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    convs = {s["sample_id"]: F.Conversation(s["conversation"], emb) for s in data}

    def qv(text):
        v = np.asarray(next(iter(emb.embed([text]))), dtype=np.float32)
        return v / np.linalg.norm(v)

    def labels(it, conv):
        e = {x.strip() for x in it["evidence"] for x in str(x).split(";") if x.strip()}
        return e, np.array([1.0 if t["id"] in e else 0.0 for t in conv.turns], dtype=np.float32)

    t0 = time.perf_counter()
    X, Y = [], []
    for it in train:
        conv = convs[it["sample_id"]]
        X.append(features(conv, it["question"], qv(it["question"])))
        Y.append(labels(it, conv)[1])
    X, Y = np.concatenate(X), np.concatenate(Y)
    model = MLP(N_FEAT, args.hidden)
    model.fit(X, Y, epochs=args.epochs)
    print(f"trained on {len(train)} non-test questions ({len(Y)} turns) in {time.perf_counter() - t0:.0f}s")

    rows = []
    for it in test:
        conv = convs[it["sample_id"]]
        e, _ = labels(it, conv)
        if not e:
            continue
        t = time.perf_counter()  # whole query: embed the question, score every turn, fill the budget
        got, used = select(conv, model.predict(features(conv, it["question"], qv(it["question"]))), args.tokens)
        secs = time.perf_counter() - t
        got = set(got)
        rows.append({"cat": it["category"], "recall": len(e & got) / len(e), "all": e <= got, "secs": secs,
                     "tokens": used})
    lat = sorted(r["secs"] for r in rows)
    print(f"TEST learned ranker, budget {args.tokens} tokens: n={len(rows)} "
          f"evidence recall {100 * sum(r['recall'] for r in rows) / len(rows):.1f}%  "
          f"all found {100 * sum(r['all'] for r in rows) / len(rows):.1f}%  "
          f"tokens used {sum(r['tokens'] for r in rows) / len(rows):.0f}  "
          f"latency p50 {1000 * lat[len(lat) // 2]:.1f} ms p95 {1000 * lat[int(len(lat) * .95)]:.1f} ms max {1000 * lat[-1]:.1f} ms")
    for cat in sorted({r["cat"] for r in rows}):
        sub = [r for r in rows if r["cat"] == cat]
        print(f"  category {cat}: recall {100 * sum(r['recall'] for r in sub) / len(sub):5.1f}%  n={len(sub)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
