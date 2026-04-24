"""Transaction cost model.

Baked into the rebalance loop so reported returns are always net of costs
at the chosen `bps` rate. The robustness battery (Phase 4) re-runs the
engine at {0, 5, 10, 25, 50} bps rather than post-hoc subtracting.

Model for monthly, overlapping-tranche portfolios:
  - Each month, one new tranche is formed and one old tranche is retired.
  - New tranche's one-sided formation turnover = gross_exposure_of_tranche.
  - Retiring tranche's one-sided exit turnover = gross_exposure_of_tranche.
  - Tranche weight in portfolio = 1/K, so per-month portfolio cost =
    (1/K) × (2 × gross) × bps/10000 = (2 × gross / K) × bps/10000 (one-sided,
    where round-trip is buy + sell of the formed leg + covering of the short).

  gross = 2 for long-short, 1 for long-only.

This is a linear approximation — no impact, no bid-ask beyond the bps
ticker, no borrow for shorts. For equity momentum at the demo scale it is
a well-accepted first-order model. More realistic impact costs are a
Phase 4 extension on top.
"""

from __future__ import annotations


def per_period_cost(
    gross_exposure: float,
    holding_months: int,
    bps: float,
) -> float:
    """Return the per-month cost as a decimal fraction to subtract from gross ret.

    Example: long-short (gross=2), K=6, bps=10 → 2 × 2 / 6 × 10/10000 = 0.000667
    (~6.7 bps/month ~ 80 bps/year). Consistent with published momentum cost drags.
    """
    if holding_months < 1:
        raise ValueError("holding_months must be >= 1")
    return (2.0 * gross_exposure / holding_months) * (bps / 10000.0)
