"""Map a paper's spec to the closest Ken French factor and compare the
paper's claimed monthly return against the realized factor return over
the same sample window.

This is a *falsification test*, not a replication. We don't reconstruct
the paper's portfolio; we ask whether the published factor — which is
the gold-standard external benchmark — actually delivered what the
paper claims it did, in the paper's own window.
"""

from __future__ import annotations

import math
from datetime import date

from src.factor_compare.loader import (
    daily_to_monthly,
    load_ff3_monthly,
    load_mom_daily,
)
from src.specs import (
    FactorComparison,
    HeadlineClaim,
    KFFactor,
    ReplicationSpec,
)


def map_signal_to_kf_factor(spec: ReplicationSpec) -> KFFactor | None:
    """Map a ReplicationSpec to the closest Ken French factor.

    Returns None when no clean mapping exists (e.g. multi-factor or
    custom signals like the AQR variance-ratio paper).
    """
    sig = spec.signal
    long_high = sig.direction == "long_high"
    if sig.kind == "past_return":
        # Carhart momentum (winners minus losers). KF MOM is constructed
        # long-high prior return, so a long_low spec would be inverse.
        return "MOM" if long_high else None
    # Value: book-to-market or P/B → HML
    if sig.kind == "fundamentals_ratio":
        formula = (sig.formula or "").lower()
        if "book" in formula and "market" in formula:
            return "HML" if long_high else None
        if "earnings" in formula or "profitability" in formula or "roe" in formula:
            return "RMW" if long_high else None
    # Size: market cap (small minus big)
    if sig.kind == "market_cap" or "market_cap" in (sig.formula or "").lower():
        # SMB is small-minus-big, so a long-low (small) spec maps to SMB.
        return "SMB" if not long_high else None
    return None


def _factor_series(factor: KFFactor):
    """Return a monthly pandas Series of decimal returns for `factor`."""
    if factor == "MOM":
        # Compound the daily MOM file into month-end monthly returns.
        daily = load_mom_daily()
        return daily_to_monthly(daily, "Mom")
    # Everything else lives in the FF3 monthly file or the FF5 daily file.
    ff3 = load_ff3_monthly()
    if factor in ("Mkt-RF", "SMB", "HML"):
        return ff3[factor]
    # RMW/CMA need the FF5 daily file compounded.
    if factor in ("RMW", "CMA"):
        from src.factor_compare.loader import load_ff5_daily
        daily = load_ff5_daily()
        return daily_to_monthly(daily, factor)
    raise ValueError(f"unknown factor {factor}")


def _verdict(claimed: float | None, realized: float, std: float, n: int) -> tuple[str, str]:
    """Return (verdict_kind, prose summary) given claim vs realized."""
    if claimed is None:
        return ("supported", (
            f"KF factor realized {realized * 100:+.3f}%/mo over the paper's window "
            f"(n={n}). No paper-claimed value was extracted, so no comparison performed."
        ))
    se = std / math.sqrt(max(n, 1)) if n > 0 else float("inf")
    gap = abs(realized - claimed)
    same_sign = (realized >= 0) == (claimed >= 0)
    # Significance threshold: claim within ±2 standard errors of realized mean.
    within_2se = gap <= 2 * se
    if not same_sign:
        return ("falsified", (
            f"Paper claims {claimed * 100:+.3f}%/mo but KF factor realized "
            f"{realized * 100:+.3f}%/mo over the same window — opposite signs. "
            f"The published factor falsifies the paper's directional claim."
        ))
    if within_2se:
        return ("supported", (
            f"KF factor realized {realized * 100:+.3f}%/mo (n={n}) — within "
            f"±2 SE ({2 * se * 100:.3f}%) of paper's claimed {claimed * 100:+.3f}%/mo. "
            f"Paper's headline is consistent with the published factor."
        ))
    return ("directional", (
        f"Same direction but magnitude differs: paper claims "
        f"{claimed * 100:+.3f}%/mo, KF factor realized {realized * 100:+.3f}%/mo "
        f"(gap {gap * 100:.3f}%/mo, {gap / se:.1f}× standard error). "
        f"Paper may overstate the factor's strength in this window."
    ))


def compare_to_kf_factor(
    spec: ReplicationSpec,
    claim: HeadlineClaim | None = None,
) -> FactorComparison:
    """Compute the realized KF factor return over the paper's window and
    compare to the paper's claim. Always returns a `FactorComparison`,
    even when no factor mapping or no overlap is possible (verdict=
    `no_factor_match` / `out_of_range`).
    """
    factor = map_signal_to_kf_factor(spec)
    if factor is None:
        return FactorComparison(
            paper_id=spec.paper_id,
            paper_title=spec.paper_title,
            factor_name="MOM",  # placeholder; ignored by callers when verdict=no_factor_match
            factor_source="—",
            paper_start=spec.start_date,
            paper_end=spec.end_date,
            n_months=0,
            verdict="no_factor_match",
            verdict_summary=(
                f"signal.kind='{spec.signal.kind}' (direction={spec.signal.direction}) "
                f"does not map cleanly to a Ken French factor. Skipping factor comparison."
            ),
        )

    series = _factor_series(factor)
    factor_min = series.index.min().date()
    factor_max = series.index.max().date()
    used_start = max(spec.start_date, factor_min)
    used_end = min(spec.end_date, factor_max)
    if used_start >= used_end:
        return FactorComparison(
            paper_id=spec.paper_id,
            paper_title=spec.paper_title,
            factor_name=factor,
            factor_source=f"Ken French Data Library — {factor}",
            paper_start=spec.start_date,
            paper_end=spec.end_date,
            used_start=None,
            used_end=None,
            n_months=0,
            verdict="out_of_range",
            verdict_summary=(
                f"Paper window {spec.start_date}→{spec.end_date} has no overlap "
                f"with KF {factor} coverage ({factor_min}→{factor_max})."
            ),
        )

    sliced = series.loc[str(used_start):str(used_end)].dropna()
    n = int(len(sliced))
    realized_mean = float(sliced.mean())
    realized_std = float(sliced.std(ddof=1)) if n > 1 else 0.0
    realized_sharpe_ann = (
        (realized_mean / realized_std) * math.sqrt(12.0) if realized_std > 0 else 0.0
    )
    realized_tstat = (
        realized_mean / (realized_std / math.sqrt(n)) if realized_std > 0 and n > 0 else 0.0
    )

    claimed_value = claim.monthly_return if claim else None
    claimed_tstat = claim.t_stat if claim else None

    verdict, summary = _verdict(claimed_value, realized_mean, realized_std, n)

    abs_gap = (realized_mean - claimed_value) if claimed_value is not None else None
    rel_gap_pct = (
        (abs_gap / claimed_value) * 100.0
        if abs_gap is not None and claimed_value not in (None, 0)
        else None
    )

    return FactorComparison(
        paper_id=spec.paper_id,
        paper_title=spec.paper_title,
        factor_name=factor,
        factor_source=f"Ken French Data Library — {factor}",
        paper_start=spec.start_date,
        paper_end=spec.end_date,
        used_start=used_start,
        used_end=used_end,
        n_months=n,
        claimed_monthly_return=claimed_value,
        claimed_tstat=claimed_tstat,
        realized_monthly_return=realized_mean,
        realized_std_monthly=realized_std,
        realized_sharpe_annualized=realized_sharpe_ann,
        realized_tstat=realized_tstat,
        verdict=verdict,
        verdict_summary=summary,
        abs_gap=abs_gap,
        relative_gap_pct=rel_gap_pct,
    )
