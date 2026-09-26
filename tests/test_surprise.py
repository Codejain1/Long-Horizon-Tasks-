import pytest

from horizon.config import SurpriseWeights
from horizon.memrouter.surprise import compute_surprise, efficiency, metric_ratio
from horizon.models import Actual, Predicted

EPS = 1e-9


def test_formula_matches_spec():
    # efficiency = mean(min(1, 1000/2000), min(1, 0.10/0.05), min(1, 500/1000)) = (0.5 + 1 + 0.5) / 3
    predicted = Predicted(success=0.8, tokens=1000, cost_usd=0.10, latency_ms=500)
    actual = Actual(success=1.0, tokens=2000, cost_usd=0.05, latency_ms=1000)
    r = compute_surprise(0.8, predicted, actual)
    eff = 2 / 3
    assert r.efficiency == pytest.approx(eff)
    assert r.outcome_score == pytest.approx(0.6 * 1.0 + 0.4 * eff)
    assert r.predicted_score == pytest.approx(0.6 * 0.8 + 0.4)
    assert r.surprise == pytest.approx(r.outcome_score - r.predicted_score)


def test_sign_follows_direction():
    p = Predicted(success=0.5, tokens=100)
    better = compute_surprise(0.5, p, Actual(success=1.0, tokens=100))
    worse = compute_surprise(0.5, p, Actual(success=0.0, tokens=100))
    as_expected = compute_surprise(0.5, p, Actual(success=0.5, tokens=100))
    assert better.surprise > 0 > worse.surprise
    assert as_expected.surprise == pytest.approx(0.0)


def test_range_is_bounded():
    worst = compute_surprise(1.0, Predicted(success=1.0, tokens=1), Actual(success=0.0, tokens=1e9))
    best = compute_surprise(0.0, Predicted(success=0.0), Actual(success=1.0))
    assert -1.0 <= worst.surprise <= 1.0 and -1.0 <= best.surprise <= 1.0
    assert best.surprise == pytest.approx(1.0)


def test_actual_zero_gives_ratio_one():
    assert metric_ratio(5.0, 0.0, EPS) == 1.0
    # Both zero also counts as on budget (open question 14; default: ratio 1).
    assert metric_ratio(0.0, 0.0, EPS) == 1.0
    assert metric_ratio(0.5, 1.0, EPS) == 0.5
    assert metric_ratio(3.0, 1.0, EPS) == 1.0


def test_missing_predicted_metric_is_dropped():
    p = Predicted(success=0.5, tokens=100)  # no cost or latency prediction
    a = Actual(success=1.0, tokens=200, cost_usd=5, latency_ms=10)
    assert efficiency(p, a, EPS) == pytest.approx(0.5)


def test_all_metrics_missing_scores_on_success_only():
    r = compute_surprise(0.7, Predicted(success=0.7), Actual(success=0.9, tokens=100))
    assert r.efficiency is None
    assert r.outcome_score == pytest.approx(0.9)
    assert r.predicted_score == pytest.approx(0.7)
    assert r.surprise == pytest.approx(0.2)


def test_weights_are_configurable():
    p, a = Predicted(success=0.5, tokens=100), Actual(success=1.0, tokens=100)
    r = compute_surprise(0.5, p, a, SurpriseWeights(success=1.0, efficiency=0.0))
    assert r.surprise == pytest.approx(0.5)
