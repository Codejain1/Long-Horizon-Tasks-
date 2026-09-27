"""Surprise = signed prediction error (MEMROUTER.md §5, step 2)."""

from __future__ import annotations

from dataclasses import dataclass

from horizon.config import SurpriseWeights
from horizon.models import Actual, Predicted

_METRICS = ("tokens", "cost_usd", "latency_ms")


@dataclass(frozen=True)
class SurpriseResult:
    surprise: float
    outcome_score: float
    predicted_score: float
    efficiency: float | None  # None when no metric had both a prediction and an actual


def metric_ratio(predicted: float, actual: float, epsilon: float) -> float:
    """``min(1, predicted / max(actual, ε))``; an actual of 0 always gives 1 (§5 edge case)."""
    if actual <= epsilon:
        return 1.0
    return min(1.0, predicted / max(actual, epsilon))


def efficiency(predicted: Predicted, actual: Actual, epsilon: float) -> float | None:
    ratios = []
    for name in _METRICS:
        p, a = getattr(predicted, name), getattr(actual, name)
        if p is None or a is None:
            continue  # predicted (or actual) missing: drop the metric
        ratios.append(metric_ratio(p, a, epsilon))
    return sum(ratios) / len(ratios) if ratios else None


def compute_surprise(
    predicted_success: float,
    predicted: Predicted,
    actual: Actual,
    weights: SurpriseWeights = SurpriseWeights(),
    epsilon: float = 1e-9,
) -> SurpriseResult:
    """``predicted_success`` is the probability actually used (the host's, or the fallback)."""
    eff = efficiency(predicted, actual, epsilon)
    if eff is None:
        # No efficiency metric: score on success only (weights renormalised).
        outcome, expected = actual.success, predicted_success
    else:
        total = weights.success + weights.efficiency
        ws, we = weights.success / total, weights.efficiency / total
        outcome = ws * actual.success + we * eff
        expected = ws * predicted_success + we  # the prediction assumes it lands on budget
    return SurpriseResult(
        surprise=max(-1.0, min(1.0, outcome - expected)),
        outcome_score=outcome,
        predicted_score=expected,
        efficiency=eff,
    )
