"""World model v0 (docs/LONG_HORIZON.md §9): from a job's state so far, P(the job ends solved).

    python runner/forecast.py train --runs /home/hermes/fastbench2 ... --out forecast.json

Trained on finished benchmark jobs: every check event in a job's run log is a state (what the watcher knew then),
labelled with the job's final hidden-test result. Logistic regression in numpy. The replay gate (grouped by job,
5 folds) must beat the base rate on Brier, paired over jobs, before the runner may act on it: stop a job that is
very likely doomed once it has used part of its budget.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

import numpy as np

FEATURES = ("minutes", "planned", "done_frac", "failed_checks", "escalations", "replans", "pass_rate", "pass_trend",
            "has_counts")


def _counts(detail: str):
    p = re.search(r"(\d+) passed", detail or "")
    f = re.search(r"(\d+) (failed|error)", detail or "")
    if not p and not f:
        return None
    passed, failed = int(p[1]) if p else 0, int(f[1]) if f else 0
    return passed / max(passed + failed, 1)


def states(events: list[dict]) -> list[dict]:
    """The watcher's view after each check: features only from what had happened by then."""
    if not events:
        return []
    t0, out, plan, ok_steps, failed, esc, plans, rates = events[0]["t"], [], [], set(), 0, 0, 0, []
    for e in events:
        ev = e.get("event")
        if ev == "plan":
            plan, plans, ok_steps = e.get("steps") or [], plans + 1, set()
        elif ev == "decision" and e.get("option") in ("escalate", "spec_fix_deep"):
            esc += 1
        elif ev == "check":
            if e.get("ok"):
                ok_steps.add(e.get("step"))
            else:
                failed += 1
            r = _counts(e.get("detail", ""))
            if r is not None:
                rates.append(r)
            out.append({"minutes": (e["t"] - t0) / 60, "planned": len(plan),
                        "done_frac": len(ok_steps) / max(len(plan), 1), "failed_checks": failed, "escalations": esc,
                        "replans": max(plans - 1, 0), "pass_rate": rates[-1] if rates else 0.0,
                        "pass_trend": rates[-1] - rates[-2] if len(rates) > 1 else 0.0, "has_counts": float(bool(rates))})
        elif ev == "report":
            break
    return out


def matrix(rows: list[dict]) -> np.ndarray:
    return np.array([[r[f] for f in FEATURES] for r in rows], dtype=float)


class Model:
    def __init__(self, w=None, b=0.0, mu=None, sd=None):
        self.w, self.b, self.mu, self.sd = w, b, mu, sd

    def fit(self, X: np.ndarray, y: np.ndarray, l2: float = 1.0, steps: int = 400, lr: float = 0.3) -> "Model":
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        Z = (X - self.mu) / self.sd
        w, b = np.zeros(Z.shape[1]), float(np.log((y.mean() + 1e-3) / (1 - y.mean() + 1e-3)))
        for _ in range(steps):
            p = 1 / (1 + np.exp(-(Z @ w + b)))
            w -= lr * (Z.T @ (p - y) / len(y) + l2 * w / len(y))
            b -= lr * float((p - y).mean())
        self.w, self.b = w, b
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-(((X - self.mu) / self.sd) @ self.w + self.b)))

    def to_json(self, **extra) -> dict:
        return {"features": FEATURES, "w": self.w.tolist(), "b": self.b, "mu": self.mu.tolist(), "sd": self.sd.tolist(),
                **extra}

    @classmethod
    def from_json(cls, d: dict) -> "Model":
        return cls(np.array(d["w"]), d["b"], np.array(d["mu"]), np.array(d["sd"]))


def replay(jobs: list[tuple[list[dict], int]], folds: int = 5, seed: int = 7) -> dict:
    """Grouped k-fold over jobs: each job's states are predicted by a model that never saw that job. Per job, the
    mean Brier of the model vs the training folds' base rate; a paired bootstrap over jobs gives the interval."""
    idx = list(range(len(jobs)))
    random.Random(seed).shuffle(idx)
    per_job = []
    for k in range(folds):
        test = set(idx[k::folds])
        train = [jobs[i] for i in idx if i not in test]
        Xtr = matrix([s for st, _ in train for s in st])
        ytr = np.array([y for st, y in train for _ in st], dtype=float)
        m = Model().fit(Xtr, ytr)
        rate = float(np.mean([y for _, y in train]))
        for i in test:
            st, y = jobs[i]
            p = m.predict(matrix(st))
            per_job.append((float(np.mean((p - y) ** 2)), float(np.mean((rate - y) ** 2)), st, p, y))
    gain = np.array([b - a for a, b, *_ in per_job])
    rng = np.random.default_rng(seed)
    boots = [float(rng.choice(gain, len(gain)).mean()) for _ in range(2000)]
    lo, hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    return {"jobs": len(per_job), "brier_model": round(float(np.mean([a for a, *_ in per_job])), 4),
            "brier_base": round(float(np.mean([b for _, b, *_ in per_job])), 4),
            "gain": round(float(gain.mean()), 4), "gain_ci": [round(lo, 4), round(hi, 4)], "passes": lo > 0,
            "_per_job": per_job}


def doom_policy(per_job, budget_min: float = 10.0, after: float = 0.4, below: float = 0.1) -> dict:
    """What "stop when P(solved) < below after `after` of the budget" would have done in the replay."""
    stopped, wrongly, saved = 0, 0, 0.0
    for _, _, st, p, y in per_job:
        hit = next((s for s, q in zip(st, p) if s["minutes"] >= after * budget_min and q < below), None)
        if hit:
            stopped += 1
            wrongly += int(y == 1)
            saved += max(budget_min - hit["minutes"], 0)
    return {"stopped": stopped, "stopped_but_solved": wrongly, "minutes_saved": round(saved, 1)}


def load_jobs(runs: list[str]) -> list[tuple[list[dict], int]]:
    jobs = []
    for run in map(Path, runs):
        for f in sorted(run.glob("*-[0-9].jsonl")):
            for line in f.read_text().splitlines():
                rec = json.loads(line)
                log = run / "work" / f"{rec['arm']}-{rec['seed']}" / f"{rec['pos']:02d}-{rec['task']}" / "run.jsonl"
                if log.exists():
                    st = states([json.loads(x) for x in log.read_text().splitlines() if x.strip()])
                    if st:
                        jobs.append((st, int(rec["solved"])))
    return jobs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["train"])
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    jobs = load_jobs(args.runs)
    r = replay(jobs)
    per_job = r.pop("_per_job")
    policy = doom_policy(per_job)
    m = Model().fit(matrix([s for st, _ in jobs for s in st]), np.array([y for st, y in jobs for _ in st], float))
    Path(args.out).write_text(json.dumps(m.to_json(gate=r, policy=policy), indent=1))
    print(json.dumps({**r, "doom_policy": policy}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
