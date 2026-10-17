"""World model, level 1 (docs/LONG_HORIZON.md §9): from the task and the job's state so far, P(the job ends solved).

    python runner/forecast.py train --runs /home/hermes/fastbench2 ... --out forecast.json [--embed MODEL_DIR]

All ours: trained only on our run logs, labelled by hidden tests, numpy only (our fine-tuned bge encoder optional).
- Task prior: how similar past jobs ended. Goals are compared by TF-IDF cosine (or our encoder with --embed); the
  prior is a similarity-weighted solve rate shrunk to the base rate.
- State: what the watcher knew at each check, as rates (failed checks per check, done steps per planned step),
  so "busy" and "stuck" are told apart.
- One logistic fit per phase of the job (first 2 min, 2-6, after 6), then an isotonic calibration.
- Gate: replay holding out whole TASKS (a new task never borrows its own history), against the base rate, paired
  over jobs; the doom-stop policy is chosen on the replay with a cap on stopping jobs that would have been solved.
v0 (2026-10-18) had no task prior and saw "more failed checks" as good: AUC 0.44-0.68, no better than pass rate alone.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import Counter
from pathlib import Path

import numpy as np

STATE = ("minutes", "checks", "done_frac", "fail_rate", "escalations", "replans", "pass_rate", "pass_trend",
         "has_counts")
FEATURES = STATE + ("prior",)
PHASES = (0.0, 2.0, 6.0)  # minutes: each phase gets its own fit
PRIOR_WEIGHT = 2.0  # the base rate counts as this many similar jobs


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
    t0, out, plan, ok_steps, failed, checks, esc, plans, rates = events[0]["t"], [], [], set(), 0, 0, 0, 0, []
    for e in events:
        ev = e.get("event")
        if ev == "plan":
            plan, plans, ok_steps = e.get("steps") or [], plans + 1, set()
        elif ev == "decision" and e.get("option") in ("escalate", "spec_fix_deep"):
            esc += 1
        elif ev == "check":
            checks += 1
            if e.get("ok"):
                ok_steps.add(e.get("step"))
            else:
                failed += 1
            r = _counts(e.get("detail", ""))
            if r is not None:
                rates.append(r)
            out.append({"minutes": (e["t"] - t0) / 60, "checks": checks,
                        "done_frac": len(ok_steps) / max(len(plan), 1), "fail_rate": failed / checks,
                        "escalations": esc, "replans": max(plans - 1, 0), "pass_rate": rates[-1] if rates else 0.0,
                        "pass_trend": rates[-1] - rates[-2] if len(rates) > 1 else 0.0, "has_counts": float(bool(rates))})
        elif ev == "report":
            break
    return out


# ------------------------------------------------------------------ task prior
def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z0-9_]+", (text or "").lower())


class Tfidf:
    """Goal similarity without any model: TF-IDF vectors, cosine."""

    def __init__(self, goals: list[str]):
        df = Counter(w for g in goals for w in set(_words(g)))
        self.vocab = {w: i for i, w in enumerate(sorted(df))}
        self.idf = np.array([math.log((1 + len(goals)) / (1 + df[w])) + 1 for w in sorted(df)])

    def __call__(self, goals: list[str]) -> np.ndarray:
        m = np.zeros((len(goals), len(self.vocab)))
        for i, g in enumerate(goals):
            for w, c in Counter(_words(g)).items():
                if w in self.vocab:
                    m[i, self.vocab[w]] = c * self.idf[self.vocab[w]]
        return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


def prior(vec: np.ndarray, past_vecs: np.ndarray, past_y: np.ndarray, base: float, power: float = 4.0) -> float:
    """Similarity-weighted solve rate of past jobs, shrunk to the base rate (PRIOR_WEIGHT pseudo-jobs)."""
    if len(past_y) == 0:
        return base
    w = np.clip(past_vecs @ vec, 0, 1) ** power
    return float((w @ past_y + PRIOR_WEIGHT * base) / (w.sum() + PRIOR_WEIGHT))


# ------------------------------------------------------------------ model
def _logit_fit(X, y, l2=1.0, steps=500, lr=0.3):
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z = (X - mu) / sd
    w, b = np.zeros(Z.shape[1]), float(np.log((y.mean() + 1e-3) / (1 - y.mean() + 1e-3)))
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        w -= lr * (Z.T @ (p - y) / len(y) + l2 * w / len(y))
        b -= lr * float((p - y).mean())
    return {"w": w.tolist(), "b": b, "mu": mu.tolist(), "sd": sd.tolist()}


def _logit(m, X):
    return 1 / (1 + np.exp(-(((X - np.array(m["mu"])) / np.array(m["sd"])) @ np.array(m["w"]) + m["b"])))


def _isotonic_points(xs, ys):
    """Pool-adjacent-violators as (edges, values), so the map is stored in JSON."""
    pooled = {}
    for x, y in zip(xs, ys):
        s = pooled.setdefault(round(float(x), 3), [0.0, 0.0])
        s[0] += y
        s[1] += 1
    out = []
    for x, (sy, n) in sorted(pooled.items()):
        out.append([sy, n, x])
        while len(out) > 1 and out[-2][0] / out[-2][1] > out[-1][0] / out[-1][1]:
            sy2, n2, _ = out.pop()
            out[-1][0] += sy2
            out[-1][1] += n2
    return [b[2] for b in out], [b[0] / b[1] for b in out]


def _apply_iso(edges, vals, p):
    i = np.clip(np.searchsorted(np.array(edges), p, side="right") - 1, 0, len(vals) - 1)
    return np.array(vals)[i]


def _phase(minutes: float) -> int:
    return sum(minutes >= t for t in PHASES) - 1


def _rows(st: list[dict], pri: float) -> np.ndarray:
    return np.array([[s[f] for f in STATE] + [pri] for s in st], dtype=float)


class Forecaster:
    def __init__(self, spec: dict | None = None, embed=None):
        self.spec, self.embed = spec or {}, embed

    # training -------------------------------------------------------
    def fit(self, jobs: list[tuple[str, list[dict], int]], calibrate: bool = True) -> "Forecaster":
        goals, ys = [g for g, _, _ in jobs], np.array([y for _, _, y in jobs], float)
        base = float(ys.mean())
        vec = self._vectorizer(goals)
        V = vec(goals)
        # each job's prior from the OTHER tasks only (no job sees its own task's outcomes)
        pri = [prior(V[i], V[[j for j in range(len(jobs)) if goals[j] != goals[i]]],
                     ys[[j for j in range(len(jobs)) if goals[j] != goals[i]]], base) for i in range(len(jobs))]
        models = []
        for k in range(len(PHASES)):
            X = [r for (g, st, y), p in zip(jobs, pri) for s, r in zip(st, _rows(st, p)) if _phase(s["minutes"]) == k]
            Y = [y for (g, st, y), p in zip(jobs, pri) for s in st if _phase(s["minutes"]) == k]
            models.append(_logit_fit(np.array(X), np.array(Y, float)) if len(set(Y)) > 1 else None)
        self.spec = {"features": FEATURES, "phases": PHASES, "models": models, "base": base,
                     "goals": goals, "outcomes": ys.tolist(), "embed": bool(self.embed)}
        if calibrate:  # isotonic map fitted on out-of-fold predictions, so it corrects real over/under-confidence
            p, y = _out_of_fold(jobs, self.embed)
            self.spec["iso"] = _isotonic_points(p, y)
        self._V, self._vec = V, vec
        return self

    def _vectorizer(self, goals):
        return self.embed if self.embed else Tfidf(goals)

    # prediction -----------------------------------------------------
    def task_prior(self, goal: str) -> float:
        if not hasattr(self, "_V"):
            self._vec = self._vectorizer(self.spec["goals"])
            self._V = self._vec(self.spec["goals"])
        ys = np.array(self.spec["outcomes"])
        keep = np.array([g != goal for g in self.spec["goals"]])
        return prior(self._vec([goal])[0], self._V[keep], ys[keep], self.spec["base"])

    def predict(self, goal: str, st: list[dict], pri: float | None = None) -> np.ndarray:
        pri = self.task_prior(goal) if pri is None else pri
        X = _rows(st, pri)
        out = np.empty(len(st))
        for i, s in enumerate(st):
            m = self.spec["models"][_phase(s["minutes"])]
            out[i] = _logit(m, X[i:i + 1])[0] if m else pri
        if self.spec.get("iso"):
            out = _apply_iso(*self.spec["iso"], out)
        return out


def _out_of_fold(jobs, embed, folds: int = 5, seed: int = 7):
    """Predictions for every state from a model that never saw that job's TASK (grouped by goal text)."""
    tasks = sorted({g for g, _, _ in jobs})
    random.Random(seed).shuffle(tasks)
    p_all, y_all = [], []
    for k in range(folds):
        test = set(tasks[k::folds])
        train = [j for j in jobs if j[0] not in test]
        f = Forecaster(embed=embed).fit(train, calibrate=False)
        for g, st, y in jobs:
            if g in test:
                p_all += list(f.predict(g, st))
                y_all += [y] * len(st)
    return p_all, y_all


def auc(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y)
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def replay(jobs, embed=None, folds: int = 5, seed: int = 7) -> dict:
    """Task-held-out replay: AUC and Brier per phase, vs the base rate; paired gain over jobs with a bootstrap."""
    tasks = sorted({g for g, _, _ in jobs})
    random.Random(seed).shuffle(tasks)
    per_job = []
    for k in range(folds):
        test = set(tasks[k::folds])
        train = [j for j in jobs if j[0] not in test]
        f = Forecaster(embed=embed).fit(train)
        rate = float(np.mean([y for _, _, y in train]))
        for g, st, y in jobs:
            if g in test:
                p = f.predict(g, st)
                per_job.append({"goal": g, "st": st, "p": p, "y": y, "brier": float(np.mean((p - y) ** 2)),
                                "base": float(np.mean((rate - y) ** 2))})
    gain = np.array([j["base"] - j["brier"] for j in per_job])
    rng = np.random.default_rng(seed)
    boots = [float(rng.choice(gain, len(gain)).mean()) for _ in range(2000)]
    phases = {}
    for k, lo in enumerate(PHASES):
        ps = [(q, j["y"]) for j in per_job for s, q in zip(j["st"], j["p"]) if _phase(s["minutes"]) == k]
        if ps:
            phases[f"from {lo:g} min"] = {"states": len(ps), "auc": round(auc(*zip(*ps)), 3),
                                          "brier": round(float(np.mean([(q - y) ** 2 for q, y in ps])), 4)}
    return {"jobs": len(per_job), "tasks": len(tasks), "brier": round(float(np.mean([j["brier"] for j in per_job])), 4),
            "brier_base": round(float(np.mean([j["base"] for j in per_job])), 4), "gain": round(float(gain.mean()), 4),
            "gain_ci": [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)],
            "passes": float(np.percentile(boots, 2.5)) > 0, "phases": phases, "_per_job": per_job}


def choose_policy(per_job, budget: float = 10.0, max_false: float = 0.02) -> dict:
    """The doom stop (stop once P(solved) < below after `after` of the budget) that saves the most minutes while
    stopping at most `max_false` of the jobs that were in fact solved. None if no policy saves anything."""
    solved = max(sum(j["y"] for j in per_job), 1)
    best = None
    for after in (0.2, 0.3, 0.4, 0.5):
        for below in (0.05, 0.1, 0.15, 0.2, 0.25):
            stopped = wrong = 0
            saved = 0.0
            for j in per_job:
                hit = next((s for s, q in zip(j["st"], j["p"]) if s["minutes"] >= after * budget and q < below), None)
                if hit:
                    stopped += 1
                    wrong += j["y"]
                    saved += max(budget - hit["minutes"], 0)
            if stopped and wrong / solved <= max_false and (best is None or saved > best["minutes_saved"]):
                best = {"after": after, "below": below, "stopped": stopped, "stopped_but_solved": wrong,
                        "minutes_saved": round(saved, 1)}
    return best or {}


def load_jobs(runs: list[str]) -> list[tuple[str, list[dict], int]]:
    jobs = []
    for run in map(Path, runs):
        for f in sorted(run.glob("*-[0-9].jsonl")):
            for line in f.read_text().splitlines():
                rec = json.loads(line)
                log = run / "work" / f"{rec['arm']}-{rec['seed']}" / f"{rec['pos']:02d}-{rec['task']}" / "run.jsonl"
                if log.exists():
                    events = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
                    goal = next((e.get("goal") for e in events if e.get("event") == "report"), None) or rec["task"]
                    st = states(events)
                    if st:
                        jobs.append((goal, st, int(rec["solved"])))
    return jobs


def load(path: str | Path) -> Forecaster | None:
    """The model the runner may act on: only one whose gate passed and that has a doom policy."""
    spec = json.loads(Path(path).read_text())
    if spec.get("embed"):  # trained on our encoder's similarities: the TF-IDF fallback would feed it other priors
        return None
    return Forecaster(spec) if spec.get("gate", {}).get("passes") and spec.get("policy") else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["train"])
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--embed", default="", help="our fine-tuned encoder (memrouter Encoder) instead of TF-IDF")
    args = ap.parse_args()
    embed = None
    if args.embed:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memrouter"))
        from memory_service import Encoder
        enc = Encoder(args.embed)
        embed = lambda texts: enc(list(texts))  # noqa: E731
    jobs = load_jobs(args.runs)
    r = replay(jobs, embed)
    per_job = r.pop("_per_job")
    policy = choose_policy(per_job)
    f = Forecaster(embed=embed).fit(jobs)
    Path(args.out).write_text(json.dumps({**f.spec, "gate": r, "policy": policy}))
    print(json.dumps({**r, "policy": policy}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
