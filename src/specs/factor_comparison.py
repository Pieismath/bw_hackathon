"""FactorComparison — the result of comparing a paper's claimed headline
return to the realized Ken French factor over the same window.

This is *not* a replication: we don't reconstruct the paper's exact
portfolio. We pick the closest published factor and ask whether the
factor itself realized the paper's claim over the paper's sample. It's
a falsification test — if even the published factor didn't deliver
what the paper claimed, the paper's claim is suspect.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


KFFactor = Literal["MOM", "HML", "SMB", "Mkt-RF", "RMW", "CMA"]
ComparisonVerdict = Literal[
    "supported",       # KF factor mean within ±2σ of claim, same sign
    "directional",     # same sign but magnitude differs materially
    "falsified",       # opposite sign or claim outside data window
    "out_of_range",    # paper window has no overlap with KF data
    "no_factor_match", # paper signal doesn't map to a KF factor
]


class FactorComparison(BaseModel):
    """Output of `compare_to_kf_factor`. Schema-frozen for the API."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paper_id: str
    paper_title: str
    factor_name: KFFactor
    factor_source: str = Field(
        description="Filename or descriptor of the KF series used.",
    )

    # Paper's window
    paper_start: date
    paper_end: date

    # Window actually used (intersection of paper window + factor coverage)
    used_start: date | None = None
    used_end: date | None = None
    n_months: int = Field(ge=0)

    # Paper's claimed numbers (if extracted)
    claimed_monthly_return: float | None = None
    claimed_tstat: float | None = None
    claimed_sharpe: float | None = None

    # Realized KF stats over the paper's window
    realized_monthly_return: float | None = None
    realized_std_monthly: float | None = None
    realized_sharpe_annualized: float | None = None
    realized_tstat: float | None = None

    # Verdict
    verdict: ComparisonVerdict
    verdict_summary: str = Field(min_length=1, max_length=500)

    # Magnitude of disagreement (decimal/mo) — None when not computable.
    abs_gap: float | None = None
    relative_gap_pct: float | None = None
