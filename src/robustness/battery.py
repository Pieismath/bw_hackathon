"""Step 11.8 — Robustness battery driver.

Single entry point that runs every stress family in order, computes the
scorecard-level summary stats (cost threshold, lag half-life, capacity),
and returns a `RobustnessScorecard`.

Each family's engine reruns are individually cached at the spec-hash
level by `run_backtest_cached`, so re-running the battery on a tweaked
spec only repeats the engine work for genuinely-changed runs.
"""

from __future__ import annotations

from typing import Iterable

from src.agents.validation import run_backtest_cached
from src.data.store import PointInTimeDataStore
from src.robustness.capacity import estimate_capacity
from src.robustness.costs import compute_cost_threshold_bps, run_cost_sweep
from src.robustness.data_quality import run_data_quality_check
from src.robustness.lag import compute_lag_half_life, run_lag_sweep
from src.robustness.liquidity import run_liquidity_sweep
from src.robustness.subperiod import run_subperiod_battery
from src.specs import (
    BacktestResult,
    ReplicationSpec,
    RobustnessScorecard,
    StressTestFamily,
    StressTestResult,
)

ALL_FAMILIES: tuple[StressTestFamily, ...] = (
    "lag",          # first — informs D3's signal_type
    "costs",
    "subperiod",
    "liquidity",
    "data_quality",
    "capacity",     # last — uses helpers from earlier steps
)


def _fragility_signals(
    tests: list[StressTestResult],
    cost_threshold: float | None,
    lag_half_life: float | None,
) -> tuple[str, ...]:
    """Render compact human-readable failure-mode bullets from the test log."""
    sigs: list[str] = []

    # Cost fragility
    if cost_threshold is not None:
        if cost_threshold < 5:
            sigs.append(f"alpha breaks at <5 bps costs (threshold ≈ {cost_threshold:.1f} bps)")
        elif cost_threshold < 10:
            sigs.append(f"thin cost margin: alpha hits zero at {cost_threshold:.1f} bps")
        elif cost_threshold == float("inf"):
            sigs.append("alpha survives all swept cost levels (≤50 bps)")

    # Subperiod fragility
    sub_failed = [
        t for t in tests
        if t.family == "subperiod" and not t.surviving and t.headline_metric < 0
    ]
    if len(sub_failed) >= 2:
        names = ", ".join(t.name.removeprefix("subperiod_") for t in sub_failed[:4])
        sigs.append(f"alpha sign-flips in {len(sub_failed)} subperiods: {names}")

    # Liquidity fragility — alpha dropping as min_price rises
    liq = sorted(
        [t for t in tests if t.family == "liquidity"],
        key=lambda t: t.parameter_swept.get("min_price", 0),
    )
    if len(liq) >= 2:
        first, last = liq[0].headline_metric, liq[-1].headline_metric
        if first > 0 and last < 0.5 * first:
            sigs.append(
                f"liquidity-sensitive: mean return falls from {first*100:+.2f}%/mo "
                f"at min_price={liq[0].parameter_swept['min_price']} "
                f"to {last*100:+.2f}%/mo at min_price={liq[-1].parameter_swept['min_price']}"
            )

    # Lag fragility
    if lag_half_life is not None and lag_half_life < 5:
        sigs.append(f"alpha decays fast: half-life ≈ {lag_half_life:.1f} business days")

    return tuple(sigs)


def run_battery(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    families: Iterable[StressTestFamily] | None = None,
    transaction_cost_bps_for_subperiod_and_liquidity: float = 0.0,
) -> RobustnessScorecard:
    """Run the full robustness battery.

    `families` defaults to ALL_FAMILIES. Pass a subset for fast iteration.
    """
    selected: tuple[StressTestFamily, ...] = (
        tuple(families) if families is not None else ALL_FAMILIES
    )

    # Baseline
    baseline = run_backtest_cached(spec, store, transaction_cost_bps=0.0)

    all_tests: list[StressTestResult] = []
    cost_threshold: float | None = None
    lag_half_life: float | None = None
    capacity_aum: float | None = None

    if "lag" in selected:
        lag_results = run_lag_sweep(spec, store)
        all_tests.extend(lag_results)
        lag_half_life = compute_lag_half_life(lag_results)

    if "costs" in selected:
        cost_results = run_cost_sweep(spec, store)
        all_tests.extend(cost_results)
        cost_threshold = compute_cost_threshold_bps(cost_results)

    if "subperiod" in selected:
        all_tests.extend(
            run_subperiod_battery(
                spec,
                store,
                transaction_cost_bps=transaction_cost_bps_for_subperiod_and_liquidity,
            )
        )

    if "liquidity" in selected:
        all_tests.extend(
            run_liquidity_sweep(
                spec,
                store,
                transaction_cost_bps=transaction_cost_bps_for_subperiod_and_liquidity,
            )
        )

    if "data_quality" in selected:
        all_tests.extend(run_data_quality_check(baseline))

    if "capacity" in selected:
        cap_results = estimate_capacity(spec, store, baseline)
        all_tests.extend(cap_results)
        # First capacity result's headline_metric is the AUM in dollars.
        if cap_results and cap_results[0].headline_metric > 0:
            capacity_aum = cap_results[0].headline_metric

    n_surviving = sum(1 for t in all_tests if t.surviving)
    fragility = _fragility_signals(all_tests, cost_threshold, lag_half_life)

    return RobustnessScorecard(
        baseline_mean_return=baseline.mean_return,
        baseline_tstat=baseline.alpha_tstat,
        baseline_n_periods=baseline.n_periods,
        tests=tuple(all_tests),
        n_tests=len(all_tests),
        n_surviving=n_surviving,
        families_run=selected,
        fragility_signals=fragility,
        cost_threshold_bps=cost_threshold,
        lag_half_life_days=lag_half_life,
        capacity_estimate_usd=capacity_aum,
    )
