"""Tests for src/robustness/.

Pure-math helpers (lag half-life, cost threshold) and engine-free schema /
boundary tests. The full battery is exercised by the JT integration script.
"""

from __future__ import annotations

import pytest

from src.robustness import (
    compute_cost_threshold_bps,
    compute_lag_half_life,
)
from src.specs import StressTestResult


def _result(name: str, family: str, value: float, **swept) -> StressTestResult:
    return StressTestResult(
        name=name,
        family=family,
        parameter_swept=swept,
        headline_metric=value,
        headline_tstat=2.0,
        n_periods=120,
        surviving=value > 0,
    )


# ---------------------------------------------------------------------------
# Lag half-life
# ---------------------------------------------------------------------------

def test_lag_half_life_basic_decay():
    # baseline 0.01, falls to 0.005 between lag 5 and 10
    rs = [
        _result("lag_0d", "lag", 0.010, execution_lag_days=0),
        _result("lag_1d", "lag", 0.009, execution_lag_days=1),
        _result("lag_5d", "lag", 0.006, execution_lag_days=5),
        _result("lag_10d", "lag", 0.004, execution_lag_days=10),
        _result("lag_20d", "lag", 0.002, execution_lag_days=20),
    ]
    hl = compute_lag_half_life(rs)
    assert hl is not None
    # Crossing 0.005 happens between lag 5 (0.006) and lag 10 (0.004),
    # at exactly midpoint of the value range → 7.5
    assert hl == pytest.approx(7.5)


def test_lag_half_life_negative_baseline_returns_none():
    rs = [
        _result("lag_0d", "lag", -0.005, execution_lag_days=0),
        _result("lag_5d", "lag", -0.002, execution_lag_days=5),
    ]
    assert compute_lag_half_life(rs) is None


def test_lag_half_life_no_decay_returns_none():
    # Alpha stays above half across the entire swept range
    rs = [
        _result("lag_0d", "lag", 0.010, execution_lag_days=0),
        _result("lag_1d", "lag", 0.0095, execution_lag_days=1),
        _result("lag_5d", "lag", 0.009, execution_lag_days=5),
        _result("lag_20d", "lag", 0.008, execution_lag_days=20),
    ]
    assert compute_lag_half_life(rs) is None


def test_lag_half_life_missing_zero_returns_none():
    rs = [_result("lag_5d", "lag", 0.005, execution_lag_days=5)]
    assert compute_lag_half_life(rs) is None


# ---------------------------------------------------------------------------
# Cost threshold
# ---------------------------------------------------------------------------

def test_cost_threshold_normal_decay():
    rs = [
        _result("cost_0bps", "costs", 0.010, transaction_cost_bps=0),
        _result("cost_5bps", "costs", 0.006, transaction_cost_bps=5),
        _result("cost_10bps", "costs", 0.002, transaction_cost_bps=10),
        _result("cost_25bps", "costs", -0.005, transaction_cost_bps=25),
        _result("cost_50bps", "costs", -0.015, transaction_cost_bps=50),
    ]
    thr = compute_cost_threshold_bps(rs)
    # Crosses zero between 10bps (+0.002) and 25bps (-0.005);
    # frac = 0.002 / 0.007 = 0.2857; threshold ≈ 10 + 0.2857 × 15 ≈ 14.29
    assert thr is not None
    assert 13 < thr < 16


def test_cost_threshold_never_breaks_returns_inf():
    rs = [
        _result("cost_0bps", "costs", 0.010, transaction_cost_bps=0),
        _result("cost_50bps", "costs", 0.008, transaction_cost_bps=50),
    ]
    assert compute_cost_threshold_bps(rs) == float("inf")


def test_cost_threshold_no_positive_baseline_returns_none():
    rs = [
        _result("cost_0bps", "costs", -0.005, transaction_cost_bps=0),
        _result("cost_50bps", "costs", -0.015, transaction_cost_bps=50),
    ]
    assert compute_cost_threshold_bps(rs) is None


def test_cost_threshold_empty_returns_none():
    assert compute_cost_threshold_bps([]) is None


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------

def test_stress_test_result_rejects_negative_n_periods():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        StressTestResult(
            name="x", family="lag", parameter_swept={},
            headline_metric=0.0, headline_tstat=None, n_periods=-1,
            surviving=False,
        )


def test_stress_test_result_frozen():
    r = _result("x", "lag", 0.01, execution_lag_days=0)
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        r.surviving = False  # type: ignore[misc]
