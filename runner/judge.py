"""The judge: a yes/no question plus evidence -> a probability that is right that often (docs/LONG_HORIZON.md §7).

    python runner/judge.py report --db judge.db      # calibration per question family, held out

Any OpenAI-compatible model answers with a probability and a one-line reason. Every judgment is logged with its
question family. When the real outcome is known (hidden tests, the owner's verdict), `label` records it. With
`min_labels` labelled judgments in a family, raw probabilities go through an isotonic map to the observed rate.
The model's confidence is not trusted; the record of being right is.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

PROMPT = """You judge whether a statement is TRUE, from the evidence only. Be strict: missing evidence counts against.

QUESTION: {question}

EVIDENCE:
{evidence}

Return JSON: {{"p": <probability from 0 to 1 that the answer is YES>, "reason": "<one line>"}}"""


def isotonic(xs: list[float], ys: list[float]):
    """Pool-adjacent-violators: a monotone map raw -> observed rate."""
    pooled: dict[float, list[float]] = {}
    for x, y in zip(xs, ys):  # equal raw scores are one point: their observed rate
        pooled.setdefault(x, [0.0, 0.0])
        pooled[x][0] += y
        pooled[x][1] += 1
    out: list[list[float]] = []
    for x, (sy, n) in sorted(pooled.items()):
        out.append([sy, n, x])
        while len(out) > 1 and out[-2][0] / out[-2][1] > out[-1][0] / out[-1][1]:
            y, n_, _ = out.pop()
            out[-1][0] += y
            out[-1][1] += n_
    edges, vals = [b[2] for b in out], [b[0] / b[1] for b in out]

    def f(x: float) -> float:
        i = max(0, min(len(vals) - 1, sum(e <= x for e in edges) - 1))
        return vals[i]
    return f


class Judge:
    def __init__(self, llm, db_path: str | Path, min_labels: int = 20):
        """llm: callable(messages, json_mode=...) -> {"content": ...} (the runner's chat); None for labelling only."""
        self.llm, self.min_labels = llm, min_labels
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, timeout=30, check_same_thread=False)  # several runner processes share it
        self.db.execute("CREATE TABLE IF NOT EXISTS judgments (id INTEGER PRIMARY KEY, ts REAL, family TEXT, "
                        "question TEXT, raw REAL, reason TEXT, outcome REAL)")
        self.db.commit()
        self.lock = threading.Lock()  # one connection, several threads (the backfill asks in parallel)

    def _map(self, family: str):
        with self.lock:
            rows = self.db.execute("SELECT raw, outcome FROM judgments WHERE family = ? AND outcome IS NOT NULL",
                                   (family,)).fetchall()
        return isotonic([r[0] for r in rows], [r[1] for r in rows]) if len(rows) >= self.min_labels else None

    def ask(self, family: str, question: str, evidence: str) -> dict:
        try:
            msg = self.llm([{"role": "user", "content": PROMPT.format(question=question, evidence=evidence[-6000:])}],
                           json_mode=True)
            m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
            got = json.loads(m[0]) if m else {}
            raw = max(0.0, min(1.0, float(got.get("p", 0.5))))
            reason = str(got.get("reason", ""))[:300]
        except Exception as e:  # a broken answer is "don't know", never a crash
            raw, reason = 0.5, f"judge error: {e!r}"[:300]
        with self.lock:
            jid = self.db.execute("INSERT INTO judgments (ts, family, question, raw, reason) VALUES (?, ?, ?, ?, ?)",
                                  (time.time(), family, question[:500], raw, reason)).lastrowid
            self.db.commit()
        cal = self._map(family)
        return {"id": jid, "p": round(cal(raw), 4) if cal else raw, "raw": raw, "reason": reason, "calibrated": bool(cal)}

    def label(self, jid: int, outcome: float) -> None:
        """The real answer for judgment `jid` (1 = it was yes)."""
        with self.lock:
            self.db.execute("UPDATE judgments SET outcome = ? WHERE id = ?", (float(outcome), jid))
            self.db.commit()

    def report(self) -> list[dict]:
        """Per family, held out (2-fold cross-fit): Brier of the raw answers, of the calibrated ones and of the base
        rate, and expected calibration error raw vs calibrated. Calibration earns its keep only if it beats both."""
        out = []
        for (family,) in self.db.execute("SELECT DISTINCT family FROM judgments WHERE outcome IS NOT NULL").fetchall():
            rows = self.db.execute("SELECT raw, outcome FROM judgments WHERE family = ? AND outcome IS NOT NULL "
                                   "ORDER BY id", (family,)).fetchall()
            folds = [rows[0::2], rows[1::2]]
            raw_p, cal_p, base_p, ys = [], [], [], []
            for k in (0, 1):
                fit, test = folds[1 - k], folds[k]
                if not fit or not test:
                    continue
                f = isotonic([r[0] for r in fit], [r[1] for r in fit])
                rate = sum(r[1] for r in fit) / len(fit)
                for x, y in test:
                    raw_p.append(x), cal_p.append(f(x)), base_p.append(rate), ys.append(y)
            brier = lambda ps: round(sum((p - y) ** 2 for p, y in zip(ps, ys)) / max(len(ys), 1), 4)  # noqa: E731
            out.append({"family": family, "n": len(rows), "rate": round(sum(r[1] for r in rows) / len(rows), 3),
                        "brier_raw": brier(raw_p), "brier_calibrated": brier(cal_p), "brier_base": brier(base_p),
                        "ece_raw": ece(raw_p, ys), "ece_calibrated": ece(cal_p, ys)})
        return out


def ece(ps: list[float], ys: list[float], bins: int = 5) -> float:
    """Expected calibration error: in each probability bin, |mean predicted - observed rate|, weighted by size."""
    total, err = len(ps), 0.0
    for b in range(bins):
        idx = [i for i, p in enumerate(ps) if b / bins <= p < (b + 1) / bins or (b == bins - 1 and p == 1.0)]
        if idx:
            err += len(idx) / total * abs(sum(ps[i] for i in idx) / len(idx) - sum(ys[i] for i in idx) / len(idx))
    return round(err, 4) if total else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report"])
    ap.add_argument("--db", required=True)
    args = ap.parse_args()
    for r in Judge(None, args.db).report():
        print(f"{r['family']:12} n {r['n']:4}  yes-rate {r['rate']:.2f}  Brier raw {r['brier_raw']:.4f}  calibrated "
              f"{r['brier_calibrated']:.4f}  base rate {r['brier_base']:.4f}  ECE raw {r['ece_raw']:.3f} -> "
              f"{r['ece_calibrated']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
