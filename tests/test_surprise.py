import random

import pytest

from horizon.config import SurpriseWeights
from horizon.memrouter.surprise import compute_surprise, efficiency_error, metric_error
from horizon.models import Actual, Predicted


def test_formula_matches_spec():
    predicted = Predicted(success=0.8, tokens=1000, cost_usd=0.10, latency_ms=500)
    actual = Actual(success=1.0, tokens=2000, cost_usd=0.05, latency_ms=1000)
    r = compute_surprise(0.8, predicted, actual)
    eff = (-1.0 + 0.5 + -1.0) / 3  # tokens and latency doubled (clamped at -1), cost halved (+0.5)
    assert r.success_error == pytest.approx(0.2)
    assert r.efficiency_error == pytest.approx(eff)
    assert r.surprise == pytest.approx(0.6 * 0.2 + 0.4 * eff)


def test_metric_error_is_signed_and_clamped():
    assert metric_error(100, 80) == pytest.approx(0.2)  # cheaper than predicted: positive
    assert metric_error(100, 150) == pytest.approx(-0.5)
    assert metric_error(100, 1000) == -1.0
    assert metric_error(100, 0) == 1.0  # free: as good as it gets


def test_accurate_predictions_average_to_zero():
    """The old formula averaged below zero for a perfectly calibrated predictor (open question 34)."""
    rng = random.Random(7)
    surprises = []
    for _ in range(20000):
        p = rng.uniform(0.05, 0.95)
        tokens = rng.uniform(1000, 50000)
        cost = rng.uniform(0.01, 1.0)
        latency = rng.uniform(100, 60000)
        # Calibrated success; costs land on the prediction up to symmetric ±20% noise.
        actual = Actual(success=1.0 if rng.random() < p else 0.0, tokens=tokens * rng.uniform(0.8, 1.2),
                        cost_usd=cost * rng.uniform(0.8, 1.2), latency_ms=latency * rng.uniform(0.8, 1.2))
        predicted = Predicted(success=p, tokens=tokens, cost_usd=cost, latency_ms=latency)
        surprises.append(compute_surprise(p, predicted, actual).surprise)
    assert abs(sum(surprises) / len(surprises)) < 0.01


def test_sign_follows_direction():
    p = Predicted(success=0.5, tokens=100)
    assert compute_surprise(0.5, p, Actual(success=1.0, tokens=100)).surprise > 0
    assert compute_surprise(0.5, p, Actual(success=0.0, tokens=100)).surprise < 0
    assert compute_surprise(0.5, p, Actual(success=0.5, tokens=100)).surprise == pytest.approx(0.0)
    # Same success, cheaper than predicted: now a positive surprise (it was never rewarded before).
    assert compute_surprise(0.5, p, Actual(success=0.5, tokens=50)).surprise > 0


def test_range_is_bounded():
    worst = compute_surprise(1.0, Predicted(success=1.0, tokens=1), Actual(success=0.0, tokens=1e9))
    best = compute_surprise(0.0, Predicted(success=0.0, tokens=10), Actual(success=1.0, tokens=0))
    assert worst.surprise == pytest.approx(-1.0) and best.surprise == pytest.approx(1.0)


def test_missing_or_zero_predictions_are_dropped():
    p = Predicted(success=0.5, tokens=100, cost_usd=0.0)  # zero cost prediction and no latency: dropped
    a = Actual(success=1.0, tokens=200, cost_usd=5, latency_ms=10)
    assert efficiency_error(p, a) == pytest.approx(-1.0)
    assert efficiency_error(Predicted(tokens=100), Actual(success=1.0)) is None  # no actual either


def test_no_efficiency_metric_scores_on_success_only():
    r = compute_surprise(0.7, Predicted(success=0.7), Actual(success=0.9, tokens=100))
    assert r.efficiency_error is None
    assert r.surprise == pytest.approx(0.2)


def test_weights_are_configurable():
    p, a = Predicted(success=0.5, tokens=100), Actual(success=1.0, tokens=100)
    r = compute_surprise(0.5, p, a, SurpriseWeights(success=1.0, efficiency=0.0))
    assert r.surprise == pytest.approx(0.5)
