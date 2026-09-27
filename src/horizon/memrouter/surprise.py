"""Surprise = signed, zero-centred prediction error (MEMROUTER.md §5, step 2).

An accurate prediction gives surprise ≈ 0 on average: success and each efficiency
metric are errors against their own prediction, so better-than-predicted and
worse-than-predicted outcomes can both happen and cancel out.
"""

from __future__ import annotations

from dataclasses import dataclass

from horizon.config import SurpriseWeights
from horizon.models import Actual, Predicted

_METRICS = ("tokens", "cost_usd", "latency_ms")


@dataclass(frozen=True)
class SurpriseResult:
    surprise: float
    success_error: float
    efficiency_error: float | None  # None when no metric had a valid prediction and an actual


def metric_error(predicted: float, actual: float) -> float:
    """``clamp((predicted − actual) / predicted, −1, 1)``: positive when cheaper than predicted."""
    return max(-1.0, min(1.0, (predicted - actual) / predicted))


def efficiency_error(predicted: Predicted, actual: Actual) -> float | None:
    errors = []
    for name in _METRICS:
        p, a = getattr(predicted, name), getattr(actual, name)
        if not p or a is None:
            continue  # missing or zero prediction (or no actual): drop the metric
        errors.append(metric_error(p, a))
    return sum(errors) / len(errors) if errors else None


def compute_surprise(
    predicted_success: float,
    predicted: Predicted,
    actual: Actual,
    weights: SurpriseWeights = SurpriseWeights(),
) -> SurpriseResult:
    """``predicted_success`` is the probability actually used (the host's, or the low-confidence fallback)."""
    success_error = actual.success - predicted_success
    eff = efficiency_error(predicted, actual)
    if eff is None:
        surprise = success_error
    else:
        total = weights.success + weights.efficiency  # normalised, so the range stays −1..1
        surprise = (weights.success * success_error + weights.efficiency * eff) / total
    return SurpriseResult(surprise=max(-1.0, min(1.0, surprise)), success_error=success_error,
                          efficiency_error=eff)
