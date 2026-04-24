"""Portfolio construction — signal scores into weighted long / short buckets.

For N-bucket sorts (quintile=5, decile=10):
  bucket 1 = lowest signal scores, bucket N = highest.
  `long_bucket` and `short_bucket` pick which buckets to trade.

Weighting:
  - "equal"  → each member of a bucket gets 1/N_members of that bucket's notional.
  - "value"  → members weighted by market cap at formation_date.

NYSE breakpoints (not yet wired — would require exchange flags in the
data source which defeatbeta doesn't carry). If `use_nyse_breakpoints`
is True the engine currently falls back to all-universe breakpoints and
records it in data_quality_flags. Flag surfaced upstream.
"""

from __future__ import annotations

import pandas as pd

from src.specs.methodology import PortfolioSpec


def form_portfolio(
    spec: PortfolioSpec,
    signal: pd.Series,
    mcap_at_formation: pd.Series | None,
) -> dict[str, float]:
    """Return a dict of {symbol: signed weight}. Long weights positive, short negative.

    Weights sum to (gross_exposure / 2) on the long side and
    -(gross_exposure / 2) on the short side for long-short, or to
    gross_exposure for long-only.
    """
    clean = signal.dropna()
    if clean.empty:
        return {}
    # Rank into buckets. `qcut` with duplicates="drop" handles signal ties
    # (e.g. many zero-return stocks) by merging bucket edges.
    try:
        buckets = pd.qcut(
            clean, q=spec.n_buckets, labels=False, duplicates="drop"
        )
    except ValueError:
        # qcut raises if fewer distinct values than buckets — skip this period.
        return {}
    # qcut returns 0-indexed bucket labels. Spec uses 1-indexed.
    buckets = buckets + 1

    long_members = clean.index[buckets == spec.long_bucket]
    short_members = (
        clean.index[buckets == spec.short_bucket]
        if spec.long_short and spec.short_bucket is not None
        else pd.Index([])
    )

    gross = spec.gross_exposure
    long_gross = gross / 2.0 if spec.long_short else gross
    short_gross = gross / 2.0 if spec.long_short else 0.0

    long_weights = _weights_within_bucket(
        long_members, spec.weighting, mcap_at_formation, total=long_gross, sign=+1.0
    )
    short_weights = _weights_within_bucket(
        short_members, spec.weighting, mcap_at_formation, total=short_gross, sign=-1.0
    )
    return {**long_weights, **short_weights}


def _weights_within_bucket(
    members: pd.Index,
    weighting: str,
    mcap: pd.Series | None,
    total: float,
    sign: float,
) -> dict[str, float]:
    if len(members) == 0 or total == 0.0:
        return {}
    if weighting == "equal":
        w = total / len(members)
        return {sym: sign * w for sym in members}
    if weighting == "value":
        if mcap is None:
            raise ValueError("value weighting requires a market-cap series")
        caps = mcap.reindex(members).dropna()
        if caps.empty or caps.sum() <= 0:
            # fall back to equal weights if all caps are missing / zero
            w = total / len(members)
            return {sym: sign * w for sym in members}
        normed = caps / caps.sum()
        return {sym: sign * total * float(normed.loc[sym]) for sym in caps.index}
    if weighting == "signal_weighted":
        # Not needed for JT or the KF MOM replication — defer until a paper
        # actually requires it.
        raise NotImplementedError("signal_weighted portfolios not yet implemented")
    raise ValueError(f"unknown weighting {weighting!r}")
