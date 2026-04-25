"""Step 11.4 — Liquidity filter (top-N proxy via min_price).

Rerun the engine on a higher-min-price universe. If alpha collapses, the
strategy was leaning on penny / micro-cap stocks. A proper top-N-by-ADV
filter would be more accurate, but that requires a new universe-resolution
path on the data store; min_price is a defensible first-order proxy
(stocks > $20 are mid-cap-and-up across most of the post-1995 sample).

The result includes a `notes` string flagging that this is a proxy, so
Phase 5 reports surface the limitation.
"""

from __future__ import annotations

from src.agents.validation import run_backtest_cached
from src.data.store import PointInTimeDataStore
from src.specs import ReplicationSpec, StressTestResult

SURVIVING_TSTAT = 1.5

# Step ladder — gives D3 a sense of how alpha decays as the universe shrinks
# to more-liquid names.
DEFAULT_MIN_PRICES = (5.0, 10.0, 20.0, 50.0)


def run_liquidity_sweep(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    min_prices: tuple[float, ...] = DEFAULT_MIN_PRICES,
    transaction_cost_bps: float = 0.0,
) -> list[StressTestResult]:
    """One StressTestResult per min_price level."""
    results: list[StressTestResult] = []
    for mp in min_prices:
        new_universe = spec.universe.model_copy(update={"min_price": mp})
        mutated = spec.model_copy(update={"universe": new_universe})
        try:
            bt = run_backtest_cached(
                mutated, store, transaction_cost_bps=transaction_cost_bps
            )
        except RuntimeError as e:
            results.append(
                StressTestResult(
                    name=f"liquidity_min_price_{int(mp)}",
                    family="liquidity",
                    parameter_swept={"min_price": mp},
                    headline_metric=0.0,
                    headline_tstat=None,
                    n_periods=0,
                    surviving=False,
                    notes=f"engine error: {e}",
                )
            )
            continue
        results.append(
            StressTestResult(
                name=f"liquidity_min_price_{int(mp)}",
                family="liquidity",
                parameter_swept={"min_price": mp},
                headline_metric=bt.mean_return,
                headline_tstat=bt.alpha_tstat,
                n_periods=bt.n_periods,
                surviving=(bt.mean_return > 0 and bt.alpha_tstat > SURVIVING_TSTAT),
                notes=(
                    f"min_price={mp} as a top-N-by-ADV proxy; a true ADV-weighted "
                    "screen would be more accurate (Phase 5 follow-up)."
                ),
                spec_hash=bt.spec_hash,
            )
        )
    return results
