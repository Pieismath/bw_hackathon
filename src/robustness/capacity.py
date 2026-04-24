"""Step 11.3 — Capacity analysis.

Estimate the maximum AUM at which the strategy's average market-impact
cost reaches a target level (default 50 bps round-trip per holding).

Method (back-of-envelope, with documented assumptions):

  1. Pull the universe at the spec's most recent formation date.
  2. Compute 60-day average dollar volume (ADV) per stock = mean(close × volume).
  3. Approximate the strategy's basket size N_legs (long_bucket members at
     formation; symmetric for short).
  4. Sort holdings by ADV; take the median ADV across the basket as a
     representative liquidity floor.
  5. Apply a square-root-impact rule: for a position f = position/ADV,
     impact ≈ K × sqrt(f) bps. K ≈ 50 bps × sqrt(0.10) ≈ 16 (calibrated to
     "10% of ADV traded → ~5 bps impact, ~50 bps at f≈100%"; rough but
     order-of-magnitude defensible).
  6. Solve for AUM such that average position = f × ADV at target impact.
     AUM ≈ N × f × ADV_median / weight_per_position.

Result: one StressTestResult with the dollar-AUM estimate. A more rigorous
Almgren-Chriss model is Phase 5.5 work.
"""

from __future__ import annotations

import math
from datetime import timedelta

import duckdb
import pandas as pd

from src.data import DefeatBetaYahooSource
from src.data.store import PointInTimeDataStore
from src.engine import compute_signal, form_portfolio
from src.engine.calendar import month_ends_in
from src.specs import BacktestResult, ReplicationSpec, StressTestResult


def estimate_capacity(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    baseline: BacktestResult,
    target_impact_bps: float = 50.0,
    adv_lookback_days: int = 60,
) -> list[StressTestResult]:
    """Estimate the AUM at which average market-impact cost = target_impact_bps.

    Returns one StressTestResult containing the dollar-AUM estimate plus
    the inputs (median ADV, basket size, assumed impact constant).
    """
    # Use the latest available formation date in the spec window
    formation_dates = month_ends_in(spec.start_date, spec.end_date)
    if not formation_dates:
        return [
            _err_result(
                "no formation dates in spec range — cannot estimate capacity"
            )
        ]
    formation = formation_dates[-1]

    # Reconstruct the basket at this formation date so we know which
    # tickers we'd actually be trading at the latest run.
    panel_q = store.get_monthly_close_panel(
        (formation - pd.DateOffset(months=24)).date(), formation.date()
    )
    price_panel = panel_q.data
    universe_q = store.get_universe(
        "defeatbeta_all_equities",
        as_of_date=formation.date(),
        min_price=spec.universe.min_price,
        min_history_days=30 * (
            (spec.signal.lookback_months or 0) + spec.signal.skip_months + 1
        ),
    )
    universe = universe_q.data
    signal_series = compute_signal(spec.signal, price_panel, formation, universe)
    weights = form_portfolio(spec.portfolio, signal_series, mcap_at_formation=None)
    if not weights:
        return [_err_result("empty portfolio at most-recent formation date")]

    holdings = list(weights.keys())
    # Get ADV from raw daily prices over `adv_lookback_days`
    src = store.sources.get("defeatbeta_yahoo")
    if not isinstance(src, DefeatBetaYahooSource):
        return [_err_result("capacity needs DefeatBetaYahooSource for ADV")]
    df = src.fetch_prices(
        holdings, formation.date(), lookback_days=adv_lookback_days
    )
    if df.empty:
        return [_err_result("no daily prices available for ADV estimation")]
    df["dollar_volume"] = df["close"].astype(float) * df["volume"].astype(float)
    adv_per_stock = df.groupby("symbol")["dollar_volume"].mean()
    # Filter to non-NaN, positive ADV
    adv_per_stock = adv_per_stock[adv_per_stock > 0]
    if adv_per_stock.empty:
        return [_err_result("no positive ADV across basket")]

    median_adv = float(adv_per_stock.median())
    n_holdings = len(holdings)

    # Square-root impact model. K calibrated so that f=1.0 (full ADV)
    # gives ~50 bps impact at the per-stock level.
    K = float(target_impact_bps)  # by construction at f=1
    f_target = (target_impact_bps / K) ** 2  # = 1.0 at default; smaller if target lower
    avg_position = f_target * median_adv

    # AUM such that each position size = avg_position. With long-short and
    # equal weights summing to gross_exposure, weight per name = gross / N.
    # Position dollars = AUM × (gross / N). Set equal to avg_position.
    gross = spec.portfolio.gross_exposure
    aum_estimate = avg_position * n_holdings / gross

    return [
        StressTestResult(
            name=f"capacity_at_{int(target_impact_bps)}bps_impact",
            family="capacity",
            parameter_swept={
                "target_impact_bps": target_impact_bps,
                "adv_lookback_days": adv_lookback_days,
                "n_holdings": n_holdings,
                "median_adv_usd": median_adv,
                "f_target": f_target,
                "K_constant": K,
            },
            headline_metric=aum_estimate,
            headline_tstat=None,
            n_periods=baseline.n_periods,
            # "Surviving" doesn't really apply here — capacity is just a number.
            # Mark False so it doesn't inflate the surviving_count.
            surviving=False,
            notes=(
                f"Sqrt-impact heuristic, calibrated K={K}, f={f_target:.2f}. "
                f"AUM at target = {aum_estimate:.2e} USD. Median ADV across "
                f"{n_holdings} holdings = {median_adv:.2e} USD/day. "
                "A rigorous Almgren-Chriss model is Phase 5.5 work."
            ),
        )
    ]


def _err_result(msg: str) -> StressTestResult:
    return StressTestResult(
        name="capacity_estimate",
        family="capacity",
        parameter_swept={},
        headline_metric=0.0,
        headline_tstat=None,
        n_periods=0,
        surviving=False,
        notes=f"capacity estimation skipped: {msg}",
    )
