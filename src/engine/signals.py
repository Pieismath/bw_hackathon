"""Signal computation — the piece that turns spec + data into a per-name score.

Phase 1 implements one signal kind: `past_return` (momentum / reversal).
Extending to `fundamental_ratio` (Novy-Marx and friends) is a later-phase
task; the dispatcher below will gain a branch then. Keep the per-kind
implementations pure: take a price panel + formation_date + params, return
a Series[symbol -> score].
"""

from __future__ import annotations

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
