"""End-to-end JT pipeline: PDF -> A1 -> A2 -> hardcoded mapping -> engine.

This script is Phase 2 Step 7: prove the pipeline runs top-to-bottom on
the demo paper before we wire up agents B1 (Data Mapper) and B2 (Mapping
Verifier) in later steps. The data mapping is hardcoded for JT here —
universe = defeatbeta_all_equities, dates clipped to 1995-2020 because
defeatbeta prices start 1994-11-30.

No LLM comparison yet (D1 is Step 10). Raw numeric output plus data-
quality flags is enough to show the replication gap.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from src.agents.extraction import extract_and_verify
from src.data import DefeatBetaYahooSource, PointInTimeDataStore
from src.engine import run_backtest
from src.pdf.parser import parse_pdf

JT_PDF = Path("data/papers/jegadeesh_titman_1993_returns_to_buying_winners_and_selling_losers.pdf")
OUT_DIR = Path("outputs")

# Paper's headline claim, from Table I Panel A J=6/K=6 row (+ body text p.11).
# Source: Jegadeesh & Titman 1993, "Returns to Buying Winners and Selling Losers",
# Table I "Buy-sell" 6/6 = 0.0095 monthly, t = 3.07 (Panel A).
JT_PAPER_CLAIM_MONTHLY = 0.0095
JT_PAPER_TSTAT = 3.07
JT_PAPER_WINDOW = "Jan 1965 – Dec 1989 (300 months)"


def clip_spec_to_defeatbeta_coverage(spec):
    """Override dates + universe name for our data. Keep methodology verbatim.

    defeatbeta prices start 1994-11-30; we clip start to 1995-01 so the
    signal window (11 lookback + 1 skip = 12 months) has valid history.
    This preserves JT's methodology; only the sample window shifts, which
    is itself one of the ambiguities the extractor already flagged.
    """
    return spec.model_copy(
        update={
            "start_date": date(1995, 1, 1),
            "end_date": date(2020, 12, 31),
        }
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("STEP 7 — JT end-to-end pipeline")
    print("=" * 72)
    print("Parsing JT PDF...", flush=True)
    pdf = parse_pdf(JT_PDF)
    print(f"  {pdf.n_pages} pages parsed.")

    print("\nRunning A1 + A2 (cached)...", flush=True)
    verified = extract_and_verify(pdf, max_retries=3, use_cache=True)
    spec = verified.spec
    report = verified.report

    # --- A2 REPORT ----------------------------------------------------
    print()
    print("=" * 72)
    print("A2 VERIFICATION REPORT")
    print("=" * 72)
    print(f"overall_confidence: {report.overall_confidence}")
    print(f"n_checks:           {report.n_checks}")
    print(
        f"failed:             high={report.n_failed_high}  "
        f"medium={report.n_failed_medium}  low={report.n_failed_low}"
    )
    print(f"retry_count:        {report.retry_count}")
    print()
    for c in report.checks:
        tag = "PASS" if not c.failed else "FAIL"
        status = c.verification_status
        page_info = (
            f"page {c.verified_page}"
            if c.verified_page is not None
            else "?"
        )
        support = (
            f"supports={c.support_check.supports}"
            if c.support_check is not None
            else "support=skipped"
        )
        print(f"  [{tag}] {c.field_path:16s} [{c.severity:6s}] {status:22s} {page_info:8s} {support}")
        if c.failure_reason:
            print(f"         failure: {c.failure_reason}")

    # --- BACKTEST ENGINE ----------------------------------------------
    print()
    print("=" * 72)
    print("DATA MAPPING (hardcoded until B1/B2 in Step 9)")
    print("=" * 72)
    print("  universe_name:  defeatbeta_all_equities (hardcoded)")
    print("  dates clipped:  1995-01 to 2020-12 (defeatbeta coverage starts 1994-11)")
    print("  paper dates:    1965-01 to 1989-12 (NOT replicable — data not available)")

    clipped_spec = clip_spec_to_defeatbeta_coverage(spec)
    src = DefeatBetaYahooSource(cache_root=Path("data/cache/hf_datasets"))
    store = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": src},
        cache_dir=Path("data/cache/query_cache"),
    )

    print("\nRunning backtest engine...", flush=True)
    result = run_backtest(clipped_spec, store, transaction_cost_bps=0.0)

    print()
    print("=" * 72)
    print("SIDE-BY-SIDE COMPARISON")
    print("=" * 72)
    print(f"{'Metric':35s} {'Paper':>18s} {'Our replication':>20s}")
    print("-" * 78)
    print(
        f"{'Monthly long-short return':35s} "
        f"{JT_PAPER_CLAIM_MONTHLY*100:>17.3f}% {result.mean_return*100:>19.3f}%"
    )
    print(
        f"{'Annualized return':35s} "
        f"{JT_PAPER_CLAIM_MONTHLY*12*100:>17.2f}% {result.annualized_return*100:>19.2f}%"
    )
    print(
        f"{'t-statistic (Newey-West)':35s} "
        f"{JT_PAPER_TSTAT:>18.2f}  {result.alpha_tstat:>19.2f}"
    )
    print(f"{'Sample window':35s} {JT_PAPER_WINDOW:>18s} "
          f"{str(result.start_date)+' to '+str(result.end_date):>20s}")
    print(f"{'n_periods':35s} {'300':>18s} {result.n_periods:>20d}")
    print(f"{'Sharpe (annualized)':35s} {'n/a':>18s} {result.sharpe_ratio:>20.2f}")
    print(f"{'Max drawdown':35s} {'n/a':>18s} {result.max_drawdown*100:>19.1f}%")

    gap_monthly = result.mean_return - JT_PAPER_CLAIM_MONTHLY
    gap_pct = (result.mean_return / JT_PAPER_CLAIM_MONTHLY - 1) * 100
    print(f"\nReplication gap: {gap_monthly*100:+.3f}%/mo ({gap_pct:+.0f}% vs paper)")

    # --- DATA QUALITY FLAGS ------------------------------------------
    print()
    print("=" * 72)
    print("DATA QUALITY FLAGS")
    print("=" * 72)
    for flag in result.data_quality_flags:
        print(f"  - {flag}")

    # --- SAVE JSON ---------------------------------------------------
    out_file = OUT_DIR / "jt_end_to_end.json"
    out_file.write_text(
        json.dumps(
            {
                "verification": {
                    "overall_confidence": report.overall_confidence,
                    "n_failed_high": report.n_failed_high,
                    "retry_count": report.retry_count,
                    "checks": [
                        {
                            "field": c.field_path,
                            "severity": c.severity,
                            "status": c.verification_status,
                            "verified_page": c.verified_page,
                            "confidence": round(c.verification_confidence, 3),
                            "supports": c.support_check.supports if c.support_check else None,
                            "failed": c.failed,
                        }
                        for c in report.checks
                    ],
                },
                "backtest": {
                    "paper_monthly": JT_PAPER_CLAIM_MONTHLY,
                    "paper_tstat": JT_PAPER_TSTAT,
                    "our_monthly": round(result.mean_return, 6),
                    "our_tstat": round(result.alpha_tstat, 3),
                    "our_sharpe": round(result.sharpe_ratio, 3),
                    "our_n_periods": result.n_periods,
                    "gap_monthly": round(gap_monthly, 6),
                    "gap_pct": round(gap_pct, 2),
                },
                "data_quality_flags": list(result.data_quality_flags),
            },
            indent=2,
            default=str,
        )
    )
    print(f"\nwrote {out_file}")

    src.close()


if __name__ == "__main__":
    main()
