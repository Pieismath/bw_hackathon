"""Signal computation — the piece that turns spec + data into a per-name score.

Three native signal kinds:
  - ``past_return`` (momentum / reversal): cumulative return over
    ``[t - (lookback + skip), t - skip]``.
  - ``variance_ratio`` (AQR-style streakiness): ``Var(annual_return) /
    (12 × Var(monthly_return))`` over a long lookback window.
  - ``fundamental_ratio``: a named accounting / valuation ratio (e.g.
    gross_profitability, book_to_market, earnings_yield) computed from
    point-in-time fundamentals at formation date. Requires a
    ``FundamentalDataSource``-conforming getter passed by the caller.

Unknown ``kind`` values raise ``NotImplementedError``; the engine layer
(``run_backtest``) catches that to emit a typed ``SpecAdaptation`` and
substitute a past-return proxy. The per-kind implementations stay pure:
take a price panel (or fundamentals getter) + formation_date + params,
return a Series[symbol -> score].
"""

from __future__ import annotations

from datetime import date as _Date
from typing import Callable, Protocol, TYPE_CHECKING

import numpy as np
import pandas as pd

from src.specs.methodology import SignalSpec

if TYPE_CHECKING:
    from src.data.store import FundamentalsSnapshot


# ---------------------------------------------------------------------------
# Fundamental-ratio registry
#
# Each entry is a callable that takes a ``dict[str, float]`` of line items
# (as emitted by ``FundamentalsSnapshot.items``) and returns the ratio value
# OR ``None`` if any required input is missing / zero in a divisor position.
#
# Canonical names are snake_case and match the academic literature. A1 emits
# the canonical name in ``signal.name`` (e.g. ``"gross_profitability"``);
# the engine looks it up here. Unknown names trigger the proxy-substitution
# fallback in ``run_backtest`` with a typed ``SpecAdaptation`` so the user
# sees what happened.
#
# To support a new ratio: add a `(name, fn)` entry below and document it
# in the A1 prompt's signal-kind section. The engine picks it up
# automatically.
# ---------------------------------------------------------------------------


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None:
        return None
    if not np.isfinite(num) or not np.isfinite(den) or den == 0.0:
        return None
    return float(num) / float(den)


def _items_get(items: dict[str, float], key: str) -> float | None:
    """Look up a line item; return None on missing / non-finite."""
    v = items.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _gross_profitability(items: dict[str, float]) -> float | None:
    """Novy-Marx 2013: (revenue - cogs) / total_assets."""
    rev = _items_get(items, "revenue") or _items_get(items, "total_revenue")
    cogs = _items_get(items, "cogs") or _items_get(items, "cost_of_revenue")
    assets = _items_get(items, "total_assets")
    if rev is None or cogs is None or assets is None:
        return None
    return _safe_div(rev - cogs, assets)


def _book_to_market(items: dict[str, float]) -> float | None:
    """Fama-French value: book_equity / market_equity."""
    be = (
        _items_get(items, "book_equity")
        or _items_get(items, "stockholders_equity")
        or _items_get(items, "common_stockholders_equity")
    )
    me = _items_get(items, "market_equity") or _items_get(items, "market_cap")
    return _safe_div(be, me)


def _earnings_yield(items: dict[str, float]) -> float | None:
    """Inverse P/E: net_income / market_equity."""
    ni = _items_get(items, "net_income") or _items_get(items, "net_income_common_stockholders")
    me = _items_get(items, "market_equity") or _items_get(items, "market_cap")
    return _safe_div(ni, me)


def _asset_growth(items: dict[str, float]) -> float | None:
    """Cooper-Gulen-Schill 2008: (total_assets_t − total_assets_t-1) / total_assets_t-1.

    Requires a prior-year items snapshot in ``items['prior_total_assets']``;
    the FundamentalDataSource must populate it (defeatbeta's
    ``get_fundamentals`` doesn't today — emits None and the engine layer
    falls back). Documented separately because asset_growth is the canonical
    "investment factor" sort.
    """
    a_t = _items_get(items, "total_assets")
    a_prev = _items_get(items, "prior_total_assets")
    if a_t is None or a_prev is None or a_prev == 0:
        return None
    return (a_t - a_prev) / a_prev


def _accruals(items: dict[str, float]) -> float | None:
    """Sloan 1996: (net_income - operating_cash_flow) / total_assets."""
    ni = _items_get(items, "net_income")
    ocf = _items_get(items, "operating_cash_flow")
    assets = _items_get(items, "total_assets")
    if ni is None or ocf is None or assets is None:
        return None
    return _safe_div(ni - ocf, assets)


FUNDAMENTAL_RATIO_REGISTRY: dict[str, Callable[[dict[str, float]], float | None]] = {
    "gross_profitability": _gross_profitability,
    "book_to_market": _book_to_market,
    "earnings_yield": _earnings_yield,
    "asset_growth": _asset_growth,
    "accruals": _accruals,
}


class _FundamentalsGetterProto(Protocol):
    """Local Protocol matching ``FundamentalDataSource.get_fundamentals``.

    Defined here as well as in ``src/data/sources/base.py`` so the engine
    module has no hard dependency on the data layer's import graph at
    type-check time. Both Protocols have the same shape.
    """

    def __call__(
        self,
        ticker: str,
        as_of_date: _Date,
        period_type: str = ...,
        filing_lag_days: int | None = ...,
    ) -> "FundamentalsSnapshot | None":
        ...


def compute_signal(
    spec: SignalSpec,
    price_panel: pd.DataFrame,
    formation_date: pd.Timestamp,
    universe: list[str],
    fundamentals_getter: _FundamentalsGetterProto | None = None,
) -> pd.Series:
    """Return a Series of signal scores indexed by ticker.

    NaN entries indicate no signal is computable (insufficient history,
    missing fundamentals, etc.); callers should dropna before ranking.

    For ``kind='fundamental_ratio'``, the caller must supply
    ``fundamentals_getter`` (any callable conforming to
    ``FundamentalDataSource.get_fundamentals``). Without it,
    ``NotImplementedError`` is raised so the engine layer can substitute
    a past-return proxy + ``SpecAdaptation``.
    """
    if spec.kind == "past_return":
        return _compute_past_return(spec, price_panel, formation_date, universe)
    if spec.kind == "variance_ratio":
        return _compute_variance_ratio(spec, price_panel, formation_date, universe)
    if spec.kind == "fundamental_ratio":
        if fundamentals_getter is None:
            raise NotImplementedError(
                "signal.kind='fundamental_ratio' requires a fundamentals_getter "
                "(any FundamentalDataSource-conforming callable). The engine "
                "layer should pass store.get_fundamentals."
            )
        return _compute_fundamental_ratio(
            spec, fundamentals_getter, formation_date, universe
        )
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


def _compute_fundamental_ratio(
    spec: SignalSpec,
    fundamentals_getter: _FundamentalsGetterProto,
    formation_date: pd.Timestamp,
    universe: list[str],
) -> pd.Series:
    """Cross-section of a named fundamental ratio at ``formation_date``.

    ``spec.name`` is the canonical ratio key (lowercase snake_case) that
    indexes into ``FUNDAMENTAL_RATIO_REGISTRY``. Unknown names raise
    ``KeyError`` so the engine layer can substitute a proxy with a typed
    ``SpecAdaptation(kind='unknown_fundamental_ratio')``.

    ``fundamentals_getter`` is any callable matching the
    ``FundamentalDataSource.get_fundamentals`` shape. The engine passes
    ``store.get_fundamentals`` for the active corpus; a future
    Bridgewater data source plugs in by satisfying the same Protocol.

    Direction handling matches the other signal kinds: ``long_low``
    negates the signal so bucket N is the paper's chosen long leg.
    """
    name = (spec.name or "").strip().lower()
    fn = FUNDAMENTAL_RATIO_REGISTRY.get(name)
    if fn is None:
        raise KeyError(
            f"unknown fundamental ratio {spec.name!r}; supported names: "
            f"{sorted(FUNDAMENTAL_RATIO_REGISTRY)}. Add an implementation in "
            f"src/engine/signals.py::FUNDAMENTAL_RATIO_REGISTRY or pick the "
            f"closest match."
        )

    # formation_date is a pd.Timestamp; the fundamentals_getter expects a
    # datetime.date. Convert defensively.
    as_of: _Date = (
        formation_date.date()
        if hasattr(formation_date, "date")
        else formation_date
    )

    scores: dict[str, float] = {}
    for sym in universe:
        snap = fundamentals_getter(sym, as_of)
        if snap is None:
            continue
        value = fn(snap.items)
        if value is None or not np.isfinite(value):
            continue
        scores[sym] = float(value)
    sig = pd.Series(scores, name="signal", dtype=float)
    if spec.direction == "long_low":
        sig = -sig
    return sig
