"""D1 — Result Comparator (deterministic).

Given a BacktestResult and a list of PaperClaim objects extracted from A1's
spec (or hand-supplied for testing), produce one ClaimComparison per claim.

D1 does NOT run an LLM. It's pure math + categorical labeling. D2
(Divergence Diagnostician — separate session) is where the LLM earns its
keep, deciding which ambiguity to mutate when the gap is large.

Verdict rubric:
  match           |gap| <= tolerance
  partial         same sign AND |gap| <= 2 * tolerance
  diverged        same sign AND |gap| > 2 * tolerance
  opposite_sign   sign of replicated_value != sign of claimed_value
                    (zero counts as matching either sign for this test)
"""

from __future__ import annotations

from src.specs import (
    BacktestResult,
    ClaimComparison,
    ClaimVerdict,
    PaperClaim,
)

# Default tolerance for monthly-return-like quantities. One basis point
# per month is the threshold below which the engine's own numerical
# noise (clipping, tie-breakers in qcut) can dominate.
DEFAULT_TOLERANCE = 0.001  # 0.1% / month


def _same_sign(a: float, b: float) -> bool:
    if a == 0 or b == 0:
        return True
    return (a > 0) == (b > 0)


def classify(
    claimed: float,
    replicated: float,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ClaimVerdict:
    """Core verdict logic — extracted so tests can drive it directly."""
    gap = replicated - claimed
    if not _same_sign(replicated, claimed):
        return "opposite_sign"
    if abs(gap) <= tolerance:
        return "match"
    if abs(gap) <= 2.0 * tolerance:
        return "partial"
    return "diverged"


def compare_claim(
    claim: PaperClaim,
    replicated_value: float,
    replicated_tstat: float | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ClaimComparison:
    """Produce one ClaimComparison."""
    absolute_gap = replicated_value - claim.claimed_value
    relative_gap = (
        replicated_value / claim.claimed_value - 1.0
        if claim.claimed_value != 0
        else float("inf")
    )
    tstat_gap = None
    if replicated_tstat is not None and claim.claimed_tstat is not None:
        tstat_gap = replicated_tstat - claim.claimed_tstat
    verdict = classify(claim.claimed_value, replicated_value, tolerance)
    notes = (
        f"claimed={claim.claimed_value}, replicated={replicated_value}, "
        f"tolerance={tolerance}, verdict={verdict}"
    )
    return ClaimComparison(
        claim=claim,
        replicated_value=replicated_value,
        absolute_gap=absolute_gap,
        relative_gap=relative_gap,
        tstat_gap=tstat_gap,
        tolerance_used=tolerance,
        verdict=verdict,
        notes=notes,
    )


def compare_results_to_claims(
    result: BacktestResult,
    claims: list[PaperClaim],
    tolerance: float = DEFAULT_TOLERANCE,
    metric_map: dict[str, str] | None = None,
) -> list[ClaimComparison]:
    """Compare a BacktestResult to a list of PaperClaim objects.

    `metric_map` pairs each claim's `metric` string with a field name on
    BacktestResult. Defaults cover the headline cases; callers can
    override for non-monthly metrics.
    """
    default_map = {
        "long_short_monthly_return": "mean_return",
        "monthly_long_short_return": "mean_return",
        "annualized_return": "annualized_return",
        "sharpe_ratio": "sharpe_ratio",
    }
    mapping = {**default_map, **(metric_map or {})}
    out: list[ClaimComparison] = []
    for claim in claims:
        field_name = mapping.get(claim.metric, "mean_return")
        replicated = getattr(result, field_name)
        # The result's alpha_tstat is the nearest analog to paper t-stat
        replicated_tstat = result.alpha_tstat
        out.append(compare_claim(claim, replicated, replicated_tstat, tolerance))
    return out
