"""Step 11.5 — Execution lag sweep.

For each lag in {0, 1, 2, 3, 5, 10, 20} business days, mutate
`rebalance.execution_lag_days` and rerun the engine. Headline metric:
`lag_half_life_days` — the lag at which mean monthly return falls to 50%
of the T+0 baseline. Coarser than a daily-decomposition decay profile
but already enough to classify signal type (microstructure / information
/ structural).

Run order matters: this stress family runs first in the battery so D3's
`signal_type` field has data to chew on by the time it writes the summary.
"""

from __future__ import annotations

from src.agents.validation import run_backtest_cached
from src.data.store import PointInTimeDataStore
from src.specs import ReplicationSpec, StressTestResult

DEFAULT_LAGS = (0, 1, 2, 3, 5, 10, 20)
SURVIVING_TSTAT = 1.5


def run_lag_sweep(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    lags: tuple[int, ...] = DEFAULT_LAGS,
    transaction_cost_bps: float = 0.0,
) -> list[StressTestResult]:
    """Rerun the engine at each lag value. Returns one StressTestResult per lag."""
    results: list[StressTestResult] = []
    for lag in lags:
        new_rebalance = spec.rebalance.model_copy(update={"execution_lag_days": lag})
        mutated = spec.model_copy(update={"rebalance": new_rebalance})
        bt = run_backtest_cached(mutated, store, transaction_cost_bps=transaction_cost_bps)
        results.append(
            StressTestResult(
                name=f"lag_{lag}d",
                family="lag",
                parameter_swept={"execution_lag_days": lag},
                headline_metric=bt.mean_return,
                headline_tstat=bt.alpha_tstat,
                n_periods=bt.n_periods,
                surviving=(bt.mean_return > 0 and bt.alpha_tstat > SURVIVING_TSTAT),
                notes=f"engine rerun with execution_lag_days={lag}",
                spec_hash=bt.spec_hash,
            )
        )
    return results


def compute_lag_half_life(results: list[StressTestResult]) -> float | None:
    """Find the lag (in business days) at which mean_return decays to 50% of
    the T+0 baseline. Linear interpolation between adjacent points; None if
    alpha is non-positive at T+0 or never falls below half over the swept range.

    A `None` half-life with negative T+0 alpha → signal is `stale`.
    A `None` half-life with positive T+0 alpha that stays high → signal is `structural`.
    Caller (D3) interprets None vs numeric.
    """
    by_lag = {
        int(r.parameter_swept["execution_lag_days"]): r.headline_metric
        for r in results
    }
    if 0 not in by_lag:
        return None
    baseline = by_lag[0]
    if baseline <= 0:
        return None  # nothing to decay from
    target = 0.5 * baseline
    sorted_lags = sorted(by_lag.keys())
    for i in range(1, len(sorted_lags)):
        prev_lag = sorted_lags[i - 1]
        curr_lag = sorted_lags[i]
        prev_val = by_lag[prev_lag]
        curr_val = by_lag[curr_lag]
        if prev_val > target >= curr_val:
            # Linear interpolation
            if prev_val == curr_val:
                return float(curr_lag)
            frac = (prev_val - target) / (prev_val - curr_val)
            return prev_lag + frac * (curr_lag - prev_lag)
    return None  # never fell to half over the swept range
