"""Step 11.5 — Signal-staleness sweep.

Originally swept `rebalance.execution_lag_days` in {0,1,2,3,5,10,20}
business days. Problem: the engine rebalances on month-end-anchored
calendar, so any sub-month lag rounds to the same monthly cohort and
every row of the lag table comes back identical to baseline. Useless
for any monthly paper, which is most of them.

Now sweeps `signal.skip_months` in {0,1,2,3,6}. Same conceptual question
("how stale can the signal be before the alpha disappears?"), but the
mutation actually produces different engine output. skip_months=1 is
the canonical Jegadeesh-1990 microstructure dodge; skip_months=6 means
"use returns from t-12 to t-6, hold from t+1" — a meaningfully different
signal.

`headline_metric` is still mean monthly return. `headline_tstat` is
still Newey-West. The half-life is now expressed in months, not days
— `lag_half_life_days` keeps its name on the wire (D3 + UI consume it
opaquely) but its semantic units have shifted. Acceptable for the
hackathon; clean rename is Phase 5.5 work.

Run order matters: this stress family runs first in the battery so D3's
`signal_type` field has data to chew on by the time it writes the summary.
"""

from __future__ import annotations

from src.agents.validation import run_backtest_cached
from src.data.store import PointInTimeDataStore
from src.specs import ReplicationSpec, StressTestResult

DEFAULT_SKIPS = (0, 1, 2, 3, 6)
SURVIVING_TSTAT = 1.5


def run_lag_sweep(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    skips: tuple[int, ...] = DEFAULT_SKIPS,
    transaction_cost_bps: float = 0.0,
) -> list[StressTestResult]:
    """Rerun the engine at each `signal.skip_months` value. Returns one
    StressTestResult per skip. (Function name + family kept as 'lag' for
    backward compatibility with D3 / UI which group on family.)"""
    results: list[StressTestResult] = []
    for skip in skips:
        new_signal = spec.signal.model_copy(update={"skip_months": skip})
        mutated = spec.model_copy(update={"signal": new_signal})
        bt = run_backtest_cached(mutated, store, transaction_cost_bps=transaction_cost_bps)
        results.append(
            StressTestResult(
                name=f"skip_{skip}m",
                family="lag",
                parameter_swept={"skip_months": skip},
                headline_metric=bt.mean_return,
                headline_tstat=bt.alpha_tstat,
                n_periods=bt.n_periods,
                surviving=(bt.mean_return > 0 and bt.alpha_tstat > SURVIVING_TSTAT),
                notes=(
                    f"engine rerun with signal.skip_months={skip} "
                    f"(monthly engine ignores sub-month lags; this is the "
                    f"semantically meaningful staleness sweep)"
                ),
                spec_hash=bt.spec_hash,
            )
        )
    return results


def compute_lag_half_life(results: list[StressTestResult]) -> float | None:
    """Find the skip (in months) at which mean_return decays to 50% of the
    skip=0 baseline. Linear interpolation between adjacent points; None if
    alpha is non-positive at skip=0 or never falls below half over the
    swept range.

    A `None` half-life with negative baseline alpha → signal is `stale`.
    A `None` half-life with positive baseline alpha that stays high →
    signal is `structural`. Caller (D3) interprets None vs numeric.
    """
    by_skip = {
        int(r.parameter_swept.get("skip_months", r.parameter_swept.get("execution_lag_days", 0))): r.headline_metric
        for r in results
    }
    if 0 not in by_skip:
        return None
    baseline = by_skip[0]
    if baseline <= 0:
        return None  # nothing to decay from
    target = 0.5 * baseline
    sorted_skips = sorted(by_skip.keys())
    for i in range(1, len(sorted_skips)):
        prev_s = sorted_skips[i - 1]
        curr_s = sorted_skips[i]
        prev_val = by_skip[prev_s]
        curr_val = by_skip[curr_s]
        if prev_val > target >= curr_val:
            if prev_val == curr_val:
                return float(curr_s)
            frac = (prev_val - target) / (prev_val - curr_val)
            return prev_s + frac * (curr_s - prev_s)
    return None  # never fell to half over the swept range
