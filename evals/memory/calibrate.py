"""Calibrate the learned ranker: does a 0.8 mean 80 %? Fit Platt scaling on half of the held-out questions,
measure on the other half, and save the correction.

    PYTHONPATH=src ~/horizon-evals/reader-venv/bin/python evals/memory/calibrate.py

Uses the dev split (ranker trained on the fit questions, embeddings fine-tuned on them: ~/horizon-evals/embed-dev).
Reports, before and after: Brier score, expected calibration error (ECE), the reliability table, and for the
staircase: when the top turn's calibrated probability is ≥ t, how often it really holds the answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import generalize as G  # noqa: E402
import learned_recall as L  # noqa: E402
import locomo  # noqa: E402

OUT = Path.home() / "horizon-evals" / "calibration.json"


def ece(p, y, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo)
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return total


def table(p, y):
    rows = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        m = (p >= lo) & (p < lo + 0.2) if lo < 0.8 else (p >= lo)
        if m.any():
            rows.append(f"{lo:.1f}-{lo + 0.2:.1f}: said {100 * p[m].mean():4.1f}% → true {100 * y[m].mean():4.1f}% (n={m.sum()})")
    return "\n    ".join(rows)


def platt(logit, y, iters=500, lr=0.1):
    a, b = 1.0, 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(a * logit + b)))
        g = p - y
        a -= lr * float((g * logit).mean())
        b -= lr * float(g.mean())
    return a, b


def isotonic(x, y):
    """Pool-adjacent-violators: a monotone map from score to probability (numpy only)."""
    order = np.argsort(x)
    xs, ys = x[order], y[order].astype(float)
    blocks = [[ys[0], 1.0, xs[0]]]
    for v, xv in zip(ys[1:], xs[1:]):
        blocks.append([v, 1.0, xv])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s1, n1, _ = blocks.pop()
            blocks[-1][0] += s1
            blocks[-1][1] += n1
    edges = np.array([blk[2] for blk in blocks])
    vals = np.array([blk[0] / blk[1] for blk in blocks])
    return lambda q: vals[np.clip(np.searchsorted(edges, q, side="right") - 1, 0, len(vals) - 1)]


def shape(logit, conv, budget=500):
    """Question-level features from the score pattern, plus whether the budget already holds all evidence."""
    p = 1 / (1 + np.exp(-logit))
    s = np.sort(p)[::-1]
    picked = L.select(conv, p, budget)[0]
    mass_in = sum(p[i] for i, t in enumerate(conv.turns) if t["id"] in set(picked)) / (p.sum() + 1e-9)
    return np.array([s[0], s[0] - s[1], s[0] - s[min(10, len(s) - 1)], (p > 0.5).sum() / 10.0, mass_in, 1.0]), picked


def main() -> int:
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
    ranker = G.train_ranker(fit, convs, engine)
    per_q = []
    for it in dev:
        conv = convs[it["sample_id"]]
        e = G.evidence(it)
        _, _, logit = ranker.forward(L.features(conv, it["question"], engine.question(it["question"])))
        y = np.array([1.0 if t["id"] in e else 0.0 for t in conv.turns])
        per_q.append((logit, y, conv, e))
    half = len(per_q) // 2
    lg_a = np.concatenate([q[0] for q in per_q[:half]])  # noqa: E501
    y_a = np.concatenate([q[1] for q in per_q[:half]])
    lg_b = np.concatenate([q[0] for q in per_q[half:]])
    y_b = np.concatenate([q[1] for q in per_q[half:]])
    raw_b = 1 / (1 + np.exp(-lg_b))
    a, b = platt(lg_a, y_a)
    cal_b = 1 / (1 + np.exp(-(a * lg_b + b)))
    print(f"held-out questions: {len(dev)} (fit correction on {half}, test on {len(dev) - half}); turns tested: {len(y_b)}")
    for name, p in (("raw ranker", raw_b), ("calibrated", cal_b)):
        print(f"  {name:11} Brier {np.mean((p - y_b) ** 2):.4f}  ECE {ece(p, y_b):.4f}\n    {table(p, y_b)}")
    # staircase view: the top turn's calibrated probability as a "confident, stop here" signal
    print("  top-1 turn holds the answer, by its calibrated probability (test half):")
    tops = []
    for logit, y, _, _ in per_q[half:]:
        j = int(np.argmax(logit))
        tops.append((1 / (1 + np.exp(-(a * logit[j] + b))), y[j]))
    tops = np.array(tops)
    for t in (0.5, 0.7, 0.8, 0.9):
        m = tops[:, 0] >= t
        if m.any():
            print(f"    p ≥ {t}: {100 * m.mean():4.1f}% of questions, top turn correct {100 * tops[m, 1].mean():4.1f}%")
    iso = isotonic(lg_a, y_a)
    iso_b = iso(lg_b)
    print(f"  isotonic    Brier {np.mean((iso_b - y_b) ** 2):.4f}  ECE {ece(iso_b, y_b):.4f}\n    {table(iso_b, y_b)}")
    # question level: does the 500-token selection already hold all the evidence? predicted from the score shape
    Xq, Yq = [], []
    for logit, y, conv, e in per_q:
        f, picked = shape(logit, conv)
        Xq.append(f)
        Yq.append(1.0 if e <= set(picked) else 0.0)
    Xq, Yq = np.array(Xq), np.array(Yq)
    w = np.zeros(Xq.shape[1])
    for _ in range(3000):  # logistic regression on the first half
        pq = 1 / (1 + np.exp(-(Xq[:half] @ w)))
        w -= 0.5 * Xq[:half].T @ (pq - Yq[:half]) / half
    pq_b = 1 / (1 + np.exp(-(Xq[half:] @ w)))
    yq_b = Yq[half:]
    print(f"  question level (test half, {len(yq_b)} Qs): all evidence already inside 500 tokens for "
          f"{100 * yq_b.mean():.1f}%;  Brier {np.mean((pq_b - yq_b) ** 2):.4f}  ECE {ece(pq_b, yq_b):.4f}")
    for t in (0.5, 0.7, 0.8, 0.9):
        m = pq_b >= t
        if m.any():
            print(f"    confidence ≥ {t}: {100 * m.mean():4.1f}% of questions stop here; of those, "
                  f"{100 * yq_b[m].mean():4.1f}% really had everything (the rest go deeper)")
    OUT.write_text(json.dumps({"turn_platt": {"a": a, "b": b}, "question_weights": w.tolist(),
                               "features": ["top1", "gap1", "gap10", "n_above_0.5/10", "mass_in_budget", "bias"],
                               "model": "embed-dev ranker"}))
    print(f"  saved correction to {OUT}: p = sigmoid({a:.3f} * logit + {b:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
