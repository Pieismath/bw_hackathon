"""Overlapping-tranche bookkeeping.

JT's "hold K months, rebalance monthly" convention implemented as K parallel
portfolios offset by one month each. At any given month t the strategy is
the average of tranches formed in months {t - K, …, t - 1} — K tranches
are active, each contributing 1/K to the strategy's exposure.

Each Tranche carries its formation and trade dates, weights at formation,
and the intended number of holding months. The main loop queries
`active_tranches_in(month, all_tranches, holding_months)` once per month.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Tranche:
    formation_date: pd.Timestamp     # the spec's rebalance month-end
    trade_date: pd.Timestamp          # formation + execution_lag_days business days
    weights: dict[str, float]          # signed: long +, short –
    holding_months: int


def active_tranches_in(
    month: pd.Timestamp,
    tranches: list[Tranche],
    holding_months: int,
) -> list[Tranche]:
    """Tranches formed in the K months preceding `month`.

    Convention: a tranche formed in month M contributes to returns in
    months {M+1, …, M+K}. So at month t, active tranches are those with
    formation_date ∈ {t - K, …, t - 1} inclusive.
    """
    if holding_months < 1:
        return []
    lower = month - pd.DateOffset(months=holding_months)
    # Semantic: a tranche formed at month-end M contributes to measurement
    # months {M+1, ..., M+K}. Equivalently at measurement month t, active
    # tranches are those formed in [t - K_months, t).
    #
    # Pandas subtlety: `t - DateOffset(months=1)` lands on the PREVIOUS
    # month's last day whenever the current month has more days than the
    # previous (e.g. 2000-03-31 - 1m = 2000-02-29). If we required
    # strict `formation_date > lower`, a tranche formed on Feb 29 would
    # be excluded at March 31 — wrong. Inclusive `>=` fixes this and
    # is correct semantically: tranche formed at (t - K) has K months of
    # life ahead.
    return [
        tr for tr in tranches
        if (tr.formation_date >= lower) and (tr.formation_date < month)
    ]
