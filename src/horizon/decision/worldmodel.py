"""The world model (PROJECT.md §6): predicts an option's consequences (success, tokens, cost, latency) in
milliseconds from logged outcomes, behind one interface so a trained model can replace it.

§6 keeps the learned world model "in the architecture from day one behind the same interface; swapped in only
when it beats the stand-ins". The pieces here:

- `WorldModel`: the interface. `HORIZON_WORLD_MODEL_CLASS=module:Class` plugs in another implementation (for
  example a Dreamer-style latent model trained on the Parquet/JSONL exports).
- `KernelWorldModel`: the first learned model. A similarity-weighted estimate over every past episode about the
  same option (archived ones included: they are training data, even though retrieval skips them), shrunk
  towards the team's base rate when evidence is thin. It needs no training step and learns with every
  outcome. The index is kept in memory and refreshed incrementally; a removal or purge forces a full reload.
- `replay`: the gate. Each past episode is predicted from strictly earlier episodes only, and the error is
  compared with what the stand-ins (Jev, the small LLM, the host) predicted for the same episodes.
"""

from __future__ import annotations

import importlib
import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import numpy as np

from horizon.db import load_json
from horizon.memrouter.spikes import same_option
from horizon.models import Condition, episode_text

TARGETS = ("tokens", "cost_usd", "latency_ms")
STAND_INS = ("jev", "llm", "sim", "host")  # prediction sources recorded on episodes that the model must beat
BANDWIDTH = 0.1  # kernel width in cosine similarity: a 0.1 drop in similarity divides the weight by e
PRIOR_WEIGHT = 1.0  # pseudo-episodes of the team's base rate
CONFIDENCE_HALF = 3.0  # effective episodes at which confidence reaches 0.5
MIN_TARGET_WEIGHT = 0.5  # effective episodes with a measured value before tokens/cost/latency are forecast
Z_ONE_SIDED_95 = 1.645


@dataclass(frozen=True)
class Forecast:
    success: float | None
    tokens: float | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    confidence: float = 0.0  # 0..1, grows with the evidence behind the forecast
    samples: float = 0.0  # effective number of past episodes behind it

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


class WorldModel(Protocol):
    name: str

    def predict(self, team_id: str, situation: str, conditions: list[Condition] | None,
                options: list[str]) -> dict[str, Forecast]: ...


def kernel_forecast(sims: np.ndarray, success: np.ndarray, targets: dict[str, np.ndarray], prior: float,
                    min_similarity: float, bandwidth: float = BANDWIDTH) -> Forecast:
    """The estimate from one option's past episodes: `sims` are their similarities to the situation."""
    sims = np.minimum(np.asarray(sims, dtype=np.float64), 1.0)  # float32 dot products can exceed 1 slightly
    w = np.where(sims >= min_similarity, np.exp((sims - 1.0) / bandwidth), 0.0)
    n = float(w.sum())
    p = (float(w @ success) + PRIOR_WEIGHT * prior) / (n + PRIOR_WEIGHT)
    out: dict[str, float | None] = {}
    for name, values in targets.items():
        known = ~np.isnan(values)
        wk = float(w[known].sum())
        out[name] = float(w[known] @ values[known]) / wk if wk >= MIN_TARGET_WEIGHT else None
    return Forecast(success=p, confidence=n / (n + CONFIDENCE_HALF), samples=n, **out)


def _vector(db, raw) -> np.ndarray:
    if db.kind == "postgres":
        return np.asarray(raw.to_numpy() if hasattr(raw, "to_numpy") else raw, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.float32)


def _num(value) -> float:
    return float("nan") if value is None else float(value)


class _Index:
    """One team's episodes as arrays, oldest first."""

    def __init__(self, dim: int):
        self.ids: set[str] = set()
        self.vectors = np.zeros((0, dim), dtype=np.float32)
        self.labels: list[str] = []
        self.success = np.zeros(0)
        self.targets = {t: np.zeros(0) for t in TARGETS}
        self.predicted: list[tuple[str, float | None, bool]] = []  # (source, success, low_confidence)
        self.created: list[str] = []
        self.fingerprint: tuple | None = None
        self.watermark = None

    def extend(self, db, rows) -> None:
        rows = [r for r in rows if r[0] not in self.ids]
        if not rows:
            return
        eps = [load_json(r[1]) for r in rows]
        self.ids.update(r[0] for r in rows)
        self.vectors = np.vstack([self.vectors, np.stack([_vector(db, r[2]) for r in rows])])
        self.labels += [e["chosen"]["label"] for e in eps]
        self.success = np.concatenate([self.success, [float(e["actual"]["success"]) for e in eps]])
        for t in TARGETS:
            self.targets[t] = np.concatenate([self.targets[t], [_num(e["actual"].get(t)) for e in eps]])
        self.predicted += [(e["predicted"].get("source", "host"), e["predicted"].get("success"),
                            bool(e.get("low_confidence"))) for e in eps]
        self.created += [str(r[3]) for r in rows]
        self.watermark = max([self.watermark, *[r[3] for r in rows]] if self.watermark is not None
                             else [r[3] for r in rows])
        order = np.argsort(np.array(self.created, dtype=object), kind="stable")
        if not np.all(order[:-1] <= order[1:]):  # archived rows can arrive out of order; keep oldest first
            self.vectors, self.success = self.vectors[order], self.success[order]
            self.targets = {t: v[order] for t, v in self.targets.items()}
            self.labels = [self.labels[i] for i in order]
            self.predicted = [self.predicted[i] for i in order]
            self.created = [self.created[i] for i in order]

    def base_rate(self, fallback: float) -> float:
        return float(self.success.mean()) if len(self.success) else fallback

    def option_mask(self, option: str) -> np.ndarray:
        return np.fromiter((same_option(label, option) for label in self.labels), bool, len(self.labels))


class KernelWorldModel:
    """Similarity-weighted outcome estimates per option over every episode of the team (see module doc)."""

    name = "kernel-v1"

    def __init__(self, router, settings):
        self.router, self.settings = router, settings
        self._indexes: dict[str, _Index] = {}

    # --- the index ---------------------------------------------------------------------------------------

    def _fingerprint(self, team_id: str) -> tuple:
        db, model = self.router.store.db, self.router.embedder.model_name
        counts = tuple(int(db.fetchone(f"SELECT COUNT(*) FROM {t} WHERE team_id = %s AND embedding_model = %s",
                                       (team_id, model))[0]) for t in ("episodes", "episodes_archive"))
        removals = int(db.fetchone("SELECT COUNT(*) FROM memory_removals WHERE team_id = %s", (team_id,))[0])
        return sum(counts), removals

    def index(self, team_id: str) -> _Index:
        """Brought up to date: new episodes are appended; a removal (or anything that shrank the store) reloads."""
        idx = self._indexes.get(team_id)
        total, removals = self._fingerprint(team_id)
        if idx is None or idx.fingerprint is None or removals != idx.fingerprint[1] or total < len(idx.ids):
            idx = self._indexes[team_id] = _Index(self.router.store.dim)
        if total != len(idx.ids):
            db, model = self.router.store.db, self.router.embedder.model_name
            since = "" if idx.watermark is None else " AND created_at >= %s"
            params = (team_id, model) + (() if idx.watermark is None else (idx.watermark,))
            rows = []
            for table in ("episodes", "episodes_archive"):
                rows += db.fetchall(f"SELECT id, data, embedding, created_at FROM {table} WHERE team_id = %s"
                                    f" AND embedding_model = %s{since}", params)
            idx.extend(db, rows)
            if len(idx.ids) != total:  # an archived episode moved between the two queries: start over once
                idx = self._indexes[team_id] = _Index(self.router.store.dim)
                rows = []
                for table in ("episodes", "episodes_archive"):
                    rows += db.fetchall(f"SELECT id, data, embedding, created_at FROM {table} WHERE team_id = %s"
                                        f" AND embedding_model = %s", (team_id, model))
                idx.extend(db, rows)
        idx.fingerprint = (total, removals)
        return idx

    # --- prediction ----------------------------------------------------------------------------------------

    def predict(self, team_id: str, situation: str, conditions: list[Condition] | None,
                options: list[str]) -> dict[str, Forecast]:
        idx = self.index(team_id)
        prior = idx.base_rate(self.settings.fallback_success_probability)
        if not len(idx.ids):
            return {o: Forecast(success=prior) for o in options}
        q = self.router.embedder.embed([episode_text(situation, conditions or [])])[0]
        sims = idx.vectors @ np.asarray(q, dtype=np.float32)
        out = {}
        for option in options:
            m = idx.option_mask(option)
            out[option] = kernel_forecast(sims[m], idx.success[m], {t: v[m] for t, v in idx.targets.items()},
                                          prior, self.settings.world_model_min_similarity)
        return out


def fill_estimates(options: list, forecasts: dict[str, dict]) -> tuple[list, list[str]]:
    """Forecast tokens/cost/latency stand in for estimates the host didn't give: only for a dimension no option
    has an estimate for and every option has a forecast for, so options are never compared across sources."""
    from dataclasses import replace

    filled = []
    for dim in TARGETS:
        values = [(forecasts.get(o.label) or {}).get(dim) for o in options]
        if all(o.estimate(dim) is None for o in options) and None not in values:
            options = [replace(o, **{f"est_{dim}": float(v)}) for o, v in zip(options, values)]
            filled.append(dim)
    return options, filled


def make_world_model(settings, router) -> WorldModel:
    """`HORIZON_WORLD_MODEL_CLASS=package.module:Class` swaps in another model; it's built as Class(router, settings)."""
    if settings.world_model_class:
        module, _, cls = settings.world_model_class.partition(":")
        return getattr(importlib.import_module(module), cls)(router, settings)
    return KernelWorldModel(router, settings)


# --- the gate: does it beat the stand-ins? --------------------------------------------------------------

def replay(idx: _Index, min_similarity: float, fallback: float, limit: int = 2000) -> dict:
    """Predict every episode from strictly earlier ones, as the model would have at the time, and compare its
    Brier score with each stand-in's recorded prediction on the same episodes. Also scores the option-blind
    version (the same kernel ignoring which option was chosen), to show what option-awareness adds."""
    n = len(idx.ids)
    start = max(0, n - limit)
    vectors = idx.vectors[start:]
    sims_all = vectors @ vectors.T if len(vectors) else np.zeros((0, 0))
    masks = {label: idx.option_mask(label)[start:] for label in set(idx.labels[start:])}
    model_sq, blind_sq, abs_err = [], [], {t: [] for t in TARGETS}
    pairs: dict[str, list[tuple[float, float]]] = {}
    for i in range(1, len(vectors)):
        g = start + i
        earlier = slice(0, i)
        prior = float(idx.success[start:g].mean()) if i else fallback
        mask = masks[idx.labels[g]][earlier]
        sims = sims_all[i, earlier]
        f = kernel_forecast(sims[mask], idx.success[start:g][mask],
                            {t: idx.targets[t][start:g][mask] for t in TARGETS}, prior, min_similarity)
        if f.samples < 1.0:
            continue  # the model has no real evidence for this option yet: it would defer to the stand-ins
        actual = float(idx.success[g])
        blind = kernel_forecast(sims, idx.success[start:g], {}, prior, min_similarity)
        model_sq.append((f.success - actual) ** 2)
        blind_sq.append((blind.success - actual) ** 2)
        for t in TARGETS:
            if getattr(f, t) is not None and not math.isnan(idx.targets[t][g]):
                abs_err[t].append(abs(getattr(f, t) - idx.targets[t][g]))
        source, p, low = idx.predicted[g]
        if source in STAND_INS and p is not None and not low:
            pairs.setdefault(source, []).append(((f.success - actual) ** 2, (p - actual) ** 2))
    mean = lambda xs: round(float(np.mean(xs)), 4) if xs else None  # noqa: E731

    def versus(v: list[tuple[float, float]]) -> dict:
        d = np.array([b - a for a, b in v])  # + = the model's squared error was lower
        se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else float("inf")
        return {"pairs": len(v), "model_brier": mean([a for a, _ in v]), "stand_in_brier": mean([b for _, b in v]),
                "advantage": round(float(d.mean()), 4), "advantage_se": round(se, 4) if math.isfinite(se) else None,
                "clearly_better": bool(d.mean() > Z_ONE_SIDED_95 * se)}

    return {
        "episodes": n, "replayed": len(vectors), "forecast": len(model_sq),
        "brier": mean(model_sq), "brier_option_blind": mean(blind_sq),
        "mae": {t: mean(v) for t, v in abs_err.items()},
        "versus": {s: versus(v) for s, v in pairs.items()},
    }


def gate(report: dict, min_pairs: int) -> dict:
    """Swapped in only when it beats the stand-ins (§6): a Brier advantage over every stand-in with enough paired
    episodes that a one-sided paired test finds clear (95 %), and at least one stand-in has enough. A lead
    that small samples could produce by luck doesn't count."""
    judged = {s: v for s, v in report["versus"].items() if v["pairs"] >= min_pairs}
    beats = {s: v["clearly_better"] for s, v in judged.items()}
    passed = bool(judged) and all(beats.values())
    if not judged:
        why = f"not enough paired outcomes yet (need {min_pairs} per stand-in)"
    elif passed:
        why = "beats " + ", ".join(sorted(beats))
    else:
        why = "doesn't beat " + ", ".join(sorted(s for s, b in beats.items() if not b))
    return {"passed": passed, "reason": why, "judged": sorted(judged)}


def evaluate(model: Any, team_id: str, settings) -> dict:
    """The replay report plus the gate, for `horizon eval-world-model` and `auto` mode."""
    if not isinstance(model, KernelWorldModel):
        return {"model": getattr(model, "name", "?"), "passed": False,
                "reason": "replay is only built for the kernel model; a plugged-in model runs with mode=on"}
    started = time.monotonic()
    report = replay(model.index(team_id), settings.world_model_min_similarity, settings.fallback_success_probability)
    return {"model": model.name, **report, **gate(report, settings.world_model_min_pairs),
            "seconds": round(time.monotonic() - started, 3)}
