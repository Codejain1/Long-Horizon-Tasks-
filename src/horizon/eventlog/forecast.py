"""World model v2 (v2 Phase 1, track C): how a session will go, from the task and the project's past sessions.

`kernel-v1` (`decision/worldmodel.py`) forecasts decision options, which lean never evaluates. This forecasts the
thing lean does have: a session. From the new task's text it predicts the chance the session ends with the tests
passing, how many failing test runs it will hit (mean and 90th percentile), its tokens (median and 90th
percentile) and turns. Phase 2 uses it as memory's surprise baseline and for stall detection; given what a
running session has already been through (`so_far`), it answers "how did similar sessions that got this far end?".

- **Evidence:** every session episode (track A) with what its event-log segment shows, weighted by similarity
  (the same kernel as `kernel-v1`) and scope (another project counts `other_project_factor` as much).
- **Prior:** with little evidence the forecast leans on a prior worth a few pseudo-sessions: the project's base
  rate, blended with Jev's reading of the task when a scorer is configured. Jev leads in a new project and fades
  as the project's own sessions accumulate; similar sessions then take over from the prior.
- **Gate:** `replay` forecasts each past session from strictly earlier ones and compares it with what memory uses
  today (the similar-episodes fallback, MEMROUTER.md §5) and the base rate, by a paired one-sided test.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from horizon.db import load_json
from horizon.decision.scorers import noul, score
from horizon.eventlog.briefing import trace
from horizon.eventlog.episodes import SessionEpisodes
from horizon.models import Condition, Predicted, episode_text

BANDWIDTH = 0.1  # as kernel-v1: a 0.1 drop in similarity divides the weight by e
BASE_PRIOR_WEIGHT = 1.0  # pseudo-sessions of the team's base rate
JEV_PRIOR_WEIGHT = 2.0  # Jev read this task; the base rate knows nothing about it
CONFIDENCE_HALF = 3.0  # effective sessions at which confidence reaches 0.5
MIN_QUANTILE_WEIGHT = 1.0  # effective sessions before a spread is given
FAILED_RUN_LEVELS = ["none", "1", "2 or 3", "4 to 6", "7 or more"]
FAILED_RUN_VALUES = [0.0, 1.0, 2.5, 5.0, 8.0]
Z_ONE_SIDED_95 = 1.645


@dataclass(frozen=True)
class SessionForecast:
    success: float
    failed_runs: float | None = None  # mean failing test runs over the session
    failed_runs_p90: float | None = None
    tokens: float | None = None  # median
    tokens_p90: float | None = None
    turns: float | None = None
    confidence: float = 0.0
    samples: float = 0.0  # effective number of past sessions behind it
    prior: str = "base_rate"  # or "jev"

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass(frozen=True)
class Prior:
    success: float
    failed_runs: float | None
    source: str
    weight: float


@dataclass
class Past:
    """One past session: its episode and what its segment showed."""

    episode_id: str
    project: str
    situation: str
    vector: np.ndarray
    success: float
    failed_runs: float
    test_runs: float
    tokens: float  # nan when unknown
    turns: float
    created: str


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    return float(v[min(int(np.searchsorted(cum, q * cum[-1])), len(v) - 1)])


def kernel(pasts: list[Past], sims: np.ndarray, project: str, prior: Prior, min_similarity: float,
           other_project: float, so_far: dict | None = None) -> SessionForecast:
    """The forecast from past sessions and their similarities to the new task."""
    if not pasts:
        return SessionForecast(success=prior.success, failed_runs=prior.failed_runs, prior=prior.source)
    sims = np.minimum(np.asarray(sims, dtype=np.float64), 1.0)
    w = np.where(sims >= min_similarity, np.exp((sims - 1.0) / BANDWIDTH), 0.0)
    w *= np.array([1.0 if p.project == project else other_project for p in pasts])
    failed = np.array([p.failed_runs for p in pasts])
    if so_far and so_far.get("failed_runs"):
        w *= failed >= so_far["failed_runs"]  # only sessions that got this far
    n = float(w.sum())
    success = np.array([p.success for p in pasts])
    k = prior.weight
    p = (float(w @ success) + k * prior.success) / (n + k)
    if prior.failed_runs is not None:
        fr = (float(w @ failed) + k * prior.failed_runs) / (n + k)
    else:
        fr = float(w @ failed) / n if n >= MIN_QUANTILE_WEIGHT else None
    out = {"failed_runs": fr}
    if n >= MIN_QUANTILE_WEIGHT:
        live = w > 0
        out["failed_runs_p90"] = weighted_quantile(failed[live], w[live], 0.9)
        tokens = np.array([p.tokens for p in pasts])
        known = live & ~np.isnan(tokens)
        if w[known].sum() >= MIN_QUANTILE_WEIGHT:
            out["tokens"] = weighted_quantile(tokens[known], w[known], 0.5)
            out["tokens_p90"] = weighted_quantile(tokens[known], w[known], 0.9)
        out["turns"] = float(w @ np.array([p.turns for p in pasts])) / n
    return SessionForecast(success=p, confidence=n / (n + CONFIDENCE_HALF), samples=n, prior=prior.source, **out)


def base_prior(pasts: list[Past], fallback: float, so_far: dict | None = None) -> Prior:
    """The team's base rate (of sessions that got as far as `so_far`, for a running session)."""
    k = (so_far or {}).get("failed_runs") or 0
    pool = [p for p in pasts if p.failed_runs >= k]
    if not pool:
        return Prior(fallback, None, "base_rate", BASE_PRIOR_WEIGHT)
    return Prior(float(np.mean([p.success for p in pool])), float(np.mean([p.failed_runs for p in pool])),
                 "base_rate", BASE_PRIOR_WEIGHT)


def prior_questions() -> dict[str, dict]:
    return {"success": noul("Will an autonomous coding agent finish `task` in one session, with the project's "
                            "tests passing at the end?"),
            "failed_runs": score("How many failing test runs will it likely hit on the way?", FAILED_RUN_LEVELS)}


def jev_prior(scorer, text: str) -> Prior:
    a = scorer.ask({"task": text}, prior_questions())
    pos = a["failed_runs"].value * (len(FAILED_RUN_VALUES) - 1)
    lo = int(min(math.floor(pos), len(FAILED_RUN_VALUES) - 2))
    runs = FAILED_RUN_VALUES[lo] + (pos - lo) * (FAILED_RUN_VALUES[lo + 1] - FAILED_RUN_VALUES[lo])
    return Prior(float(a["success"].value), float(runs), "jev", JEV_PRIOR_WEIGHT)


def query_text(text: str, host: str = "claude-code") -> str:
    """How a new task is embedded: like its episode will be (MEMROUTER.md v2 note), so similarities compare."""
    return episode_text(text, [Condition(key="task_type", value="session"), Condition(key="host", value=host)])


class SessionWorldModel:
    name = "session-v2"

    def __init__(self, log: SessionEpisodes, embedder, settings, scorer=None):
        self.log, self.embedder, self.settings, self.scorer = log, embedder, settings, scorer

    def pasts(self, team_id: str) -> list[Past]:
        """Every session episode of the team embedded with this model, oldest first."""
        # ponytail: one query per episode; batch it once teams have thousands of sessions.
        db, out = self.log.db, []
        for project, episode_id in db.fetchall(
                "SELECT project, episode_id FROM session_episodes WHERE team_id = %s AND episode_id IS NOT NULL",
                (team_id,)):
            row = None
            for table in ("episodes", "episodes_archive"):
                row = row or db.fetchone(f"SELECT data, embedding, created_at FROM {table} WHERE id = %s AND"
                                         " embedding_model = %s", (episode_id, self.embedder.model_name))
            seg = self.log.segment_of(team_id, episode_id)
            if not row or not seg:
                continue
            ep, events = load_json(row[0]), self.log.segment_events(team_id, seg)
            t = trace(events)
            tokens = ep["actual"].get("tokens")
            out.append(Past(episode_id, project, ep["situation"], _vector(db, row[1]), float(ep["actual"]["success"]),
                            float(t.failed_runs), float(t.runs), float("nan") if tokens is None else float(tokens),
                            float(sum(e.kind == "turn_end" for e in events)), str(row[2])))
        return sorted(out, key=lambda p: p.created)

    def prior(self, text: str, pasts: list[Past], project: str, so_far: dict | None = None) -> Prior:
        """Jev's reading of the task, blended with the project's base rate by how many sessions the project has:
        Jev can't know how this agent does on this codebase, the history can. A running session (`so_far`) gets
        the base rate of sessions that got as far."""
        pool = [p for p in pasts if p.project == project] or pasts
        base = base_prior(pool, self.settings.fallback_success_probability, so_far)
        if self.scorer is None or so_far:
            return base
        try:
            jev = jev_prior(self.scorer, text)
        except Exception:  # the scorer is down: the base rate still knows something
            return base
        m, j = float(len(pool)), jev.weight
        mix = lambda b, v: v if b is None else (m * b + j * v) / (m + j)  # noqa: E731
        return Prior(mix(base.success if pool else None, jev.success), mix(base.failed_runs, jev.failed_runs), "jev", j)

    def predict(self, team_id: str, project: str, text: str, so_far: dict | None = None) -> SessionForecast:
        pasts = self.pasts(team_id)
        prior = self.prior(text, pasts, project, so_far)
        if not pasts:
            return kernel([], np.zeros(0), project, prior, 0.0, 0.0)
        q = np.asarray(self.embedder.embed([query_text(text)])[0], dtype=np.float32)
        sims = np.stack([p.vector for p in pasts]) @ q
        return kernel(pasts, sims, project, prior, self.settings.world_model_min_similarity,
                      self.settings.other_project_factor, so_far)


def _vector(db, raw) -> np.ndarray:
    if db.kind == "postgres":
        return np.asarray(raw.to_numpy() if hasattr(raw, "to_numpy") else raw, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.float32)


# --- the gate: is it better than what memory uses today? --------------------------------------------------

def replay(model: SessionWorldModel, team_id: str, limit: int = 500) -> dict:
    """Forecast every past session from strictly earlier ones, as the model would have at the time. Compared on
    the     same sessions with memory's current surprise baseline (the mean success of similar earlier episodes,
    else the fallback probability) and the base rate. With a scorer, the blended prior alone is scored too."""
    s = model.settings
    pasts = model.pasts(team_id)[-limit:]
    if len(pasts) < 2:
        return {"sessions": len(pasts), "forecast": 0}
    vectors = np.stack([p.vector for p in pasts])
    sims_all = np.minimum(vectors @ vectors.T, 1.0)
    sq = {"model": [], "fallback": [], "base_rate": [], "jev_prior": []}
    fr_err = {"model": [], "base_rate": []}
    tok_err = []
    for i in range(1, len(pasts)):
        earlier, now = pasts[:i], pasts[i]
        sims = sims_all[i, :i]
        base = base_prior(earlier, s.fallback_success_probability)
        prior = model.prior(now.situation, earlier, now.project)
        jev_alone = model.scorer is not None and prior.source == "jev"
        f = kernel(earlier, sims, now.project, prior, s.world_model_min_similarity, s.other_project_factor)
        top = np.argsort(-sims)[: s.similar_top_k]
        similar = [earlier[j].success for j in top if sims[j] >= s.similar_min_similarity]
        fallback = float(np.mean(similar)) if similar else s.fallback_success_probability
        sq["model"].append((f.success - now.success) ** 2)
        sq["fallback"].append((fallback - now.success) ** 2)
        sq["base_rate"].append((base.success - now.success) ** 2)
        if jev_alone:
            sq["jev_prior"].append((prior.success - now.success) ** 2)
        if f.failed_runs is not None:
            fr_err["model"].append(abs(f.failed_runs - now.failed_runs))
            fr_err["base_rate"].append(abs((base.failed_runs or 0.0) - now.failed_runs))
        if f.tokens and not math.isnan(now.tokens) and now.tokens > 0:
            tok_err.append(abs(math.log(f.tokens / now.tokens)))
    mean = lambda xs: round(float(np.mean(xs)), 4) if xs else None  # noqa: E731
    return {
        "sessions": len(pasts), "forecast": len(sq["model"]),
        "brier": {k: mean(v) for k, v in sq.items() if v},
        "versus": {k: versus(sq["model"], sq[k]) for k in ("fallback", "base_rate")},
        "failed_runs_mae": {k: mean(v) for k, v in fr_err.items()},
        "tokens_mean_abs_log_error": mean(tok_err),
    }


def versus(model: list[float], other: list[float]) -> dict:
    """Paired: how much lower the model's squared error was, and whether that's clear at 95 % (one-sided)."""
    d = np.array(other) - np.array(model)
    se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else float("inf")
    return {"pairs": len(d), "advantage": round(float(d.mean()), 4) if len(d) else None,
            "advantage_se": round(se, 4) if math.isfinite(se) else None,
            "clearly_better": bool(len(d) > 1 and d.mean() > Z_ONE_SIDED_95 * se)}


# --- surprise: what a session episode is recorded against (Phase 2) -----------------------------------------

GATE_WINDOW = 200  # the latest sessions the gate replays


def gate(model: SessionWorldModel, team_id: str, min_pairs: int) -> bool:
    """Whether the forecast is clearly better than memory's fallback on the latest sessions' replay."""
    n = model.log.db.fetchone("SELECT COUNT(*) FROM session_episodes WHERE team_id = %s AND episode_id IS NOT NULL",
                              (team_id,))[0]
    if int(n) - 1 < min_pairs:  # not enough pairs yet: skip the replay
        return False
    v = replay(model, team_id, GATE_WINDOW).get("versus", {}).get("fallback")
    return bool(v and v["pairs"] >= min_pairs and v["clearly_better"])


def surprise_predictor(model: SessionWorldModel, team_id: str, mode: str, min_pairs: int):
    """`(project, situation) -> Predicted` for the episodes being written, or None to keep memory's fallback:
    always with "forecast", with "auto" only once the gate passes. A forecast with no evidence behind it
    (confidence 0) is no prediction: the episode stays low-confidence, as MEMROUTER.md §5 wants."""
    if mode not in ("auto", "forecast") or (mode == "auto" and not gate(model, team_id, min_pairs)):
        return None

    def predict(project: str, situation: str) -> Predicted | None:
        f = model.predict(team_id, project, situation)
        if f.confidence <= 0:
            return None
        return Predicted(success=f.success, tokens=f.tokens or None, confidence=f.confidence, source="world_model")

    return predict


def configured_predictor(settings, log: SessionEpisodes):
    """For `SessionEpisodes.write(predictor=…)`: built from the memrouter once it exists (its embedder)."""
    return lambda router: surprise_predictor(SessionWorldModel(log, router.embedder, settings), settings.team_id,
                                             settings.session_surprise, settings.world_model_min_pairs)
