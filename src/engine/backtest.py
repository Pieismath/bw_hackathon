"""run_backtest — single entry point that consumes a ReplicationSpec.

Loop shape:
  1. Pull the monthly close panel + (if needed) market cap panel from the
     store, covering the signal-warmup period plus the spec's date range.
  2. Derive monthly returns from closes.
  3. Iterate month-ends in [start, end]:
       resolve universe  → compute signal  → build tranche  → record
  4. Iterate measurement months:
       for each active tranche, compute its return this month
       strategy return = mean across active tranches, minus period costs
  5. Wrap results in BacktestResult with data_quality_flags propagated
     from the data store's provenance.

The function is pure: same (spec, store state) → same BacktestResult.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.data.store import PointInTimeDataStore
from src.engine.calendar import business_day_shift, month_ends_in
from src.engine.costs import per_period_cost
from src.engine.metrics import (
    annualize_return,
    annualize_vol,
    hit_rate,
    max_drawdown,
    newey_west_mean_tstat,
    sharpe_ratio,
)
from src.engine.portfolio import form_portfolio
from src.engine.signals import compute_signal
from src.engine.tranches import Tranche, active_tranches_in
from src.specs import (
    BacktestResult,
    FormationDecayPoint,
    ProvenanceRecord,
    ReplicationSpec,
    ReturnObservation,
)


def run_backtest(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    transaction_cost_bps: float = 0.0,
) -> BacktestResult:
    spec_hash = _hash_spec(spec)
    holding_months = spec.rebalance.holding_period_months or 1
    gross = spec.portfolio.gross_exposure

    # Warmup: need enough history before the first formation date to
    # compute the signal. For past_return: lookback + skip months. For
    # variance_ratio: lookback + skip + 12 (rolling-12m anchor needs an
    # extra year before the variance window starts).
    signal_warmup_months = 0
    if spec.signal.kind == "past_return" and spec.signal.lookback_months is not None:
        signal_warmup_months = spec.signal.lookback_months + spec.signal.skip_months
    elif spec.signal.kind == "variance_ratio" and spec.signal.lookback_months is not None:
        signal_warmup_months = spec.signal.lookback_months + spec.signal.skip_months + 12

    panel_start = (
        pd.Timestamp(spec.start_date)
        - pd.DateOffset(months=signal_warmup_months + 2)
    ).date()
    # Need `holding_months` past end_date to measure the final tranches' returns.
    panel_end = (
        pd.Timestamp(spec.end_date) + pd.DateOffset(months=holding_months + 2)
    ).date()

    price_q = store.get_monthly_close_panel(panel_start, panel_end)
    price_panel = price_q.data
    if price_panel.empty:
        raise RuntimeError("no price data in requested window")
    # Yahoo-sourced panel contains junk rows: thinly-traded stocks with $0 or
    # corrupt-split-adjusted prices can produce infinite or millions-of-percent
    # pct_change. Replace inf with NaN, then clip at ±500% — no investable US
    # equity has legitimately delivered >500% in a calendar month in the
    # post-1994 sample (GME 2021 peaked ~400% monthly). A real Phase-4 fix is
    # a dollar-volume filter at universe level; this is the engine's defensive
    # floor until then.
    returns_panel = price_panel.pct_change().replace([np.inf, -np.inf], np.nan)
    extreme_mask = returns_panel.abs() > 5.0
    extreme_count = int(extreme_mask.sum().sum())
    returns_panel = returns_panel.where(~extreme_mask)

    mcap_panel = None
    mcap_provenance = None
    if spec.portfolio.weighting == "value":
        mcap_q = store.get_market_cap_panel(panel_start, panel_end)
        mcap_panel = mcap_q.data
        mcap_provenance = mcap_q.provenance

    data_quality_flags: list[str] = []
    if spec.signal.kind == "variance_ratio":
        data_quality_flags.append(
            "variance_ratio computed on individual stocks. The AQR streaks "
            "paper sorts JKP factor portfolios (153 factors) by VR, not "
            "individual equities. The statistic is identical; the cross-"
            "section is not. Direction and concept of the paper are "
            "preserved, but the stock-level VR is a different — and noisier "
            "— object than the factor-level VR. Treat the headline number "
            "as concept-faithful, NOT as a like-for-like replication of the "
            "paper's portfolio."
        )
    if spec.portfolio.use_nyse_breakpoints:
        data_quality_flags.append(
            "NYSE breakpoints requested but exchange flags not available "
            "in defeatbeta_yahoo; falling back to all-universe breakpoints."
        )
    if spec.universe.region == "US":
        data_quality_flags.append(
            "Yahoo-sourced universe excludes delisted names — survivorship bias."
        )
    if extreme_count > 0:
        data_quality_flags.append(
            f"{extreme_count} monthly returns exceeded ±500% and were "
            "clipped to NaN (thinly-traded / data-artifact stocks)."
        )

    # --- Form tranches at each formation date --------------------------
    formation_dates = month_ends_in(spec.start_date, spec.end_date)
    tranches: list[Tranche] = []
    for formation_date in formation_dates:
        if formation_date not in price_panel.index:
            continue
        universe = store.get_universe(
            "defeatbeta_all_equities",
            as_of_date=formation_date.date(),
            min_price=spec.universe.min_price,
            min_history_days=30 * (signal_warmup_months + 1),
        ).data
        signal = compute_signal(
            spec.signal, price_panel, formation_date, universe
        )
        if signal.empty or signal.dropna().empty:
            continue
        mcap_at = None
        if mcap_panel is not None and formation_date in mcap_panel.index:
            mcap_at = mcap_panel.loc[formation_date]
        weights = form_portfolio(spec.portfolio, signal, mcap_at)
        if not weights:
            continue
        trade_date = business_day_shift(
            formation_date, spec.rebalance.execution_lag_days
        )
        tranches.append(
            Tranche(
                formation_date=formation_date,
                trade_date=trade_date,
                weights=weights,
                holding_months=holding_months,
            )
        )

    # --- Walk measurement months & aggregate tranche returns -----------
    first_measurement = formation_dates[0] if formation_dates else None
    if first_measurement is None:
        raise RuntimeError("no formation dates produced — check start/end and data coverage")
    last_measurement = (
        pd.Timestamp(spec.end_date) + pd.DateOffset(months=holding_months)
    ).to_period("M").to_timestamp("M").normalize()
    measurement_months = [
        t for t in price_panel.index
        if first_measurement <= t <= last_measurement
    ]

    cost_per_month = per_period_cost(gross, holding_months, transaction_cost_bps)
    observations: list[ReturnObservation] = []
    # Age-bucket accumulator for the post-formation decay curve: age (months
    # since formation, 1..K) → list of per-tranche gross returns. One entry
    # per (tranche, measurement-month) pair with a well-defined return.
    decay_buckets: dict[int, list[float]] = {}
    for t in measurement_months:
        active = active_tranches_in(t, tranches, holding_months)
        if not active:
            continue
        if t not in returns_panel.index:
            continue
        asset_rets_t = returns_panel.loc[t]
        tranche_rets: list[float] = []
        for tr in active:
            numer = 0.0
            denom = 0.0
            for sym, w in tr.weights.items():
                if sym not in asset_rets_t.index:
                    continue
                r = asset_rets_t[sym]
                if pd.isna(r):
                    continue
                numer += w * float(r)
                denom += abs(w)
            if denom == 0.0:
                continue
            tranche_rets.append(numer)
            # Record this tranche's gross return at its current age. Both
            # dates are month-ends from the same panel index, so a calendar
            # month-difference is exact.
            age = (t.year - tr.formation_date.year) * 12 + (t.month - tr.formation_date.month)
            if 1 <= age <= holding_months:
                decay_buckets.setdefault(age, []).append(numer)
        if not tranche_rets:
            continue
        gross_ret = sum(tranche_rets) / len(tranche_rets)
        net_ret = gross_ret - cost_per_month
        observations.append(
            ReturnObservation(
                period_end=t.date(),
                ret=net_ret,
                formation_date=None,  # aggregated across K tranches; no single formation
                rebalance_id=t.strftime("%Y-%m"),
            )
        )

    if not observations:
        raise RuntimeError("no return observations produced")

    ret_series = pd.Series(
        {pd.Timestamp(obs.period_end): obs.ret for obs in observations}
    ).sort_index()

    nw_lag = max(holding_months - 1, 0)
    mean_ret, t_mean = newey_west_mean_tstat(ret_series, nw_lag)
    ann_ret = annualize_return(mean_ret)
    vol = annualize_vol(ret_series.std())
    sharpe = sharpe_ratio(ret_series)
    dd = max_drawdown(ret_series)
    hr = hit_rate(ret_series)

    # Average per-period turnover (one-sided, as a fraction of NAV).
    avg_turnover = (gross / holding_months) if holding_months > 0 else 0.0

    # Post-formation decay curve: for each age 1..K, the cross-tranche mean
    # and sample standard error of the per-tranche gross return. Emits one
    # FormationDecayPoint per age with at least one observation.
    decay_by_age: list[FormationDecayPoint] = []
    for age in sorted(decay_buckets):
        vals = np.asarray(decay_buckets[age], dtype=float)
        n = int(vals.size)
        if n == 0:
            continue
        mean_val = float(vals.mean())
        # Sample SE of the mean; ddof=1 and guard n<2.
        se = float(vals.std(ddof=1) / np.sqrt(n)) if n >= 2 else 0.0
        decay_by_age.append(FormationDecayPoint(
            age_months=age,
            mean_ret=mean_val,
            std_error=se,
            n_observations=n,
        ))

    provenance = price_q.provenance.child(
        source_id="engine:backtest",
        source_tier="synthesized",
        as_of_date=spec.end_date,
        notes=(
            f"run_backtest spec_hash={spec_hash} "
            f"holding={holding_months}m costs={transaction_cost_bps}bps"
        ),
    )

    return BacktestResult(
        spec_hash=spec_hash,
        start_date=observations[0].period_end,
        end_date=observations[-1].period_end,
        n_periods=len(observations),
        returns=tuple(observations),
        mean_return=mean_ret,
        annualized_return=ann_ret,
        volatility=vol,
        sharpe_ratio=sharpe,
        alpha=mean_ret,  # Phase 1: alpha == mean return (vs H0=0). Market-adjusted alpha is Phase 4.
        alpha_annualized=ann_ret,
        alpha_tstat=t_mean,
        turnover=avg_turnover,
        max_drawdown=dd,
        hit_rate=hr,
        transaction_cost_bps=transaction_cost_bps,
        return_convention="arithmetic_monthly",
        newey_west_lag=nw_lag,
        decay_by_age=tuple(decay_by_age),
        data_quality_flags=tuple(data_quality_flags),
        provenance=provenance,
    )


def _hash_spec(spec: ReplicationSpec) -> str:
    """Stable content hash for cache/identity. Uses model_dump_json."""
    payload = spec.model_dump_json()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
