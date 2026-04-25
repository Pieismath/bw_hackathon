"""HeadlineClaim — the paper's reported headline result for A1's chosen variant.

A1 extracts methodology from a paper and selects ONE primary variant (per the
"single headline variant" rule in the A1 prompt). For D2 (divergence
diagnostician) to attribute the gap between the paper's claim and the engine's
replication, we also need the paper's *own* reported number for that same
variant.

`HeadlineClaim` captures it with a verbatim supporting quote — A2 verifies the
quote like any other. Distinct from `PaperClaim` in `claims.py`: PaperClaim is
the legacy contract D2 internally consumes; HeadlineClaim is the A1-extraction
shape, intentionally narrowed to monthly long-short returns (the metric the
engine actually produces).

If a paper reports no single headline number (purely theoretical work, or a
sweep with no designated primary), A1 sets `headline_claim = None`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.specs.claims import SupportingQuote


HeadlineMetric = Literal["monthly_long_short_return"]


class HeadlineClaim(BaseModel):
    """Paper-reported headline performance for the variant A1 chose as primary.

    Used by D2 as the comparison target. The supporting quote must be verbatim
    and contain the number itself so a human reviewer can confirm the linkage
    at a glance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: HeadlineMetric = "monthly_long_short_return"
    monthly_return: float = Field(
        description=(
            "Mean monthly long-short return, decimal "
            "(e.g. 0.0095 = 0.95% per month)."
        ),
    )
    t_stat: float | None = Field(
        default=None,
        description="Newey-West t-statistic for the headline number if reported.",
    )
    window_label: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "Literal sample window the number applies to — "
            "e.g. 'Jan 1965 – Dec 1989' or '1965–1989 (300 months)'."
        ),
    )
    paper_location: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Where the number appears in the paper — "
            "e.g. 'Table I Panel A, J=6/K=6 \"Buy-sell\" row'."
        ),
    )
    supporting_quote: SupportingQuote
