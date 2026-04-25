"""Signal computation — the piece that turns spec + data into a per-name score.

Phase 1 implements `past_return` (momentum / reversal). Phase 6 adds
`variance_ratio` (AQR-style streakiness — high VR means returns
auto-correlate at the annual horizon). Extending to `fundamental_ratio`
(Novy-Marx and friends) is a later-phase task; the dispatcher below will
gain a branch then. Keep the per-kind implementations pure: take a price
panel + formation_date + params, return a Series[symbol -> score].
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.specs.methodology import SignalSpec


def compute_signal(
    spec: SignalSpec,
    price_panel: pd.DataFrame,
    formation_date: pd.Timestamp,
    universe: list[str],
) -> pd.Series:
    """Return a Series of signal scores indexed by ticker.

    NaN entries indicate no signal is computable (insufficient history, etc.);
    callers should dropna before ranking.
    """
    if spec.kind == "past_return":
        return _compute_past_return(spec, price_panel, formation_date, universe)
    if spec.kind == "variance_ratio":
        return _compute_variance_ratio(spec, price_panel, formation_date, universe)
    raise NotImplementedError(f"signal kind {spec.kind!r} not yet implemented")


def _compute_past_return(
    spec: SignalSpec,
    price_panel: pd.DataFrame,
    formation_date: pd.Timestamp,
    universe: list[str],
) -> pd.Series:
    """Cumulative return over [t - (lookback + skip), t - skip].

    For JT's canonical 6-month formation with 1-month skip:
      lookback_months=6, skip_months=1 → signal = p(t-1)/p(t-7) - 1.

    If `spec.direction == "long_low"` the computed signal is negated so
    that bucket N (top of the cross-section) picks the paper's "winners"
    regardless of whether those are defined as high- or low-past-return
    stocks. See DESIGN_NOTES.md for the history of this field — it was
    previously annotation-only, fixed in this commit.

    If prices are missing at either endpoint for a ticker, the score is NaN.
    """
    if spec.lookback_months is None:
        raise ValueError("past_return signal requires lookback_months")
    lb = spec.lookback_months
    sk = spec.skip_months

    # price_panel index is calendar month-ends. Find formation row, then
    # step back `skip` and `skip + lookback` months via positional offsets
    # rather than date arithmetic so that non-trading months (unlikely for
    # monthly US data, but possible at coverage boundaries) don't break.
    idx = price_panel.index
    if formation_date not in idx:
        return pd.Series(dtype=float, name="signal")
    pos_end = idx.get_loc(formation_date) - sk
    pos_start = pos_end - lb
    if pos_start < 0:
        return pd.Series(dtype=float, name="signal")

    cols = [c for c in universe if c in price_panel.columns]
    if not cols:
        return pd.Series(dtype=float, name="signal")

    end_row = price_panel.iloc[pos_end][cols]
    start_row = price_panel.iloc[pos_start][cols]
    signal = (end_row / start_row - 1.0).rename("signal")
    if spec.direction == "long_low":
        signal = -signal
    return signal


def _compute_variance_ratio(
    spec: SignalSpec,
    price_panel: pd.DataFrame,
    formation_date: pd.Timestamp,
    universe: list[str],
) -> pd.Series:
    """AQR-style variance ratio per ticker.

    Definition (AQR 2024, "The Hidden Value of Streaky Returns", page 6):

        VR(ticker) = Var(annual_return) / (12 * Var(monthly_return))

    where the variances are computed over `lookback_months` of history
    ending at `formation_date`. The paper sorts JKP factor portfolios by
    VR; we compute the same statistic on individual stocks (the cross-
    section difference is flagged at the engine layer, not silently
    smuggled through). VR > 1 ⇒ positively-autocorrelated returns at the
    annual horizon (streaky). VR < 1 ⇒ mean-reverting. VR ≈ 1 ⇒ iid.

    Implementation choice: we use OVERLAPPING rolling-12m returns because
    requiring non-overlapping annual buckets would mean ≤5 observations
    over a 5-year lookback — too few for stable variance estimation. The
    paper uses an expanding 30+ year window with non-overlapping annuals;
    our default reflects what's feasible with a defeatbeta panel that
    starts in late-1994. The statistic is the same; the variance estimate
    is more stable but biased downward by the overlap (which affects all
    tickers equally and so does not bias the cross-sectional ranking).

    `spec.skip_months` is honored — the variance window ends at
    `formation_date - skip_months` so the most-recent (potentially
    lookback-contaminating) data can be excluded if requested. Direction
    handling matches `_compute_past_return`: `long_low` negates the
    signal so bucket N still picks the paper's "winners".

    NaN entries indicate insufficient valid history for that ticker.
    """
    if spec.lookback_months is None:
        raise ValueError("variance_ratio signal requires lookback_months")
    lb = spec.lookback_months
    sk = spec.skip_months

    idx = price_panel.index
    if formation_date not in idx:
        return pd.Series(dtype=float, name="signal")
    pos_end = idx.get_loc(formation_date) - sk
    pos_start = pos_end - lb
    # Need at least 24 monthly observations (one full annual cycle plus 12
    # rolling-12m points) — the spec validator already enforces this, but
    # we guard at the boundary too because clipping can produce a short
    # panel for early formation dates.
    if pos_start < 0 or pos_end - pos_start < 24:
        return pd.Series(dtype=float, name="signal")

    cols = [c for c in universe if c in price_panel.columns]
    if not cols:
        return pd.Series(dtype=float, name="signal")

    # Slice the lookback window. iloc end is exclusive — we want pos_end
    # inclusive so the formation-month price is the right edge.
    window = price_panel.iloc[pos_start : pos_end + 1][cols]
    # Monthly simple returns. First row is NaN (no prior price), so the
    # variance below is computed on lb valid points.
    monthly = window.pct_change()
    # Overlapping rolling 12-month compounded return per ticker, anchored
    # at month-end. The first 11 entries are NaN (need 12 months to
    # compound), giving (lb - 11) valid annual observations per ticker.
    rolling_12m = (1.0 + monthly).rolling(window=12, min_periods=12).apply(
        lambda x: x.prod() - 1.0, raw=True
    )

    var_monthly = monthly.var(ddof=1)
    var_annual = rolling_12m.var(ddof=1)

    # Guard the denominator: tickers with zero monthly variance (constant
    # price across the window — typically a delisted name with carried-
    # forward price) get NaN rather than inf.
    with np.errstate(divide="ignore", invalid="ignore"):
        vr = var_annual / (12.0 * var_monthly)
    vr = vr.replace([np.inf, -np.inf], np.nan).rename("signal")
    if spec.direction == "long_low":
        vr = -vr
    return vr
