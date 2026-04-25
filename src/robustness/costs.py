"""Step 11.2 — Transaction cost sweep.

For each `bps ∈ {0, 5, 10, 25, 50}`, rerun the engine with that cost level.
Headline: `cost_threshold_bps` — the bps at which the strategy's mean
return crosses zero (linear interpolation between the two flanking sweep
points). Strategies with `cost_threshold_bps < 10` are not realistically
implementable at retail-scale frictions.
"""

from __future__ import annotations

from src.agents.validation import run_backtest_cached
from src.data.store import PointInTimeDataStore
from src.specs import ReplicationSpec, StressTestResult

DEFAULT_BPS_LEVELS = (0.0, 5.0, 10.0, 25.0, 50.0)
SURVIVING_TSTAT = 1.5


def run_cost_sweep(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    bps_levels: tuple[float, ...] = DEFAULT_BPS_LEVELS,
) -> list[StressTestResult]:
    """One StressTestResult per bps level."""
    results: list[StressTestResult] = []
    for bps in bps_levels:
        bt = run_backtest_cached(spec, store, transaction_cost_bps=bps)
        results.append(
            StressTestResult(
                name=f"cost_{int(bps)}bps",
                family="costs",
                parameter_swept={"transaction_cost_bps": bps},
                headline_metric=bt.mean_return,
                headline_tstat=bt.alpha_tstat,
                n_periods=bt.n_periods,
                surviving=(bt.mean_return > 0 and bt.alpha_tstat > SURVIVING_TSTAT),
                notes=f"engine rerun with transaction_cost_bps={bps}",
                spec_hash=bt.spec_hash,
            )
        )
    return results


def compute_cost_threshold_bps(results: list[StressTestResult]) -> float | None:
    """Find the bps level at which mean_return crosses zero.

    Returns None if mean_return is non-positive at the lowest bps level
    (no positive alpha to erode), or +inf if it stays positive across the
    entire swept range.
    """
    pairs = sorted(
        [(float(r.parameter_swept["transaction_cost_bps"]), r.headline_metric)
         for r in results]
    )
    if not pairs:
        return None
    # No positive alpha at zero cost: nothing to find.
    if pairs[0][1] <= 0:
        return None
    # All positive across the range → never breaks
    if all(ret > 0 for _, ret in pairs):
        return float("inf")
    # Find the crossing
    for i in range(1, len(pairs)):
        prev_bps, prev_ret = pairs[i - 1]
        curr_bps, curr_ret = pairs[i]
        if prev_ret > 0 >= curr_ret:
            if prev_ret == curr_ret:  # degenerate
                return curr_bps
            frac = prev_ret / (prev_ret - curr_ret)
            return prev_bps + frac * (curr_bps - prev_bps)
    return None
