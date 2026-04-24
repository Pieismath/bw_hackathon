"""Consolidated JT pipeline: PDF -> A1 -> A2 -> A3 -> B1/B2 -> engine -> D1.

This is the Step 6+9+D1 demo. Run it end-to-end on the JT 1993 paper; it
exercises every agent built so far, reports each agent's output, and
produces a ClaimComparison against the paper's published headline.

D2 (Divergence Diagnostician) is NOT run — that's a separate focused
session, because it's the piece where spec mutation + re-runs against
the ambiguity flags actually close the gap.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.agents.extraction import (
    extract_and_verify,
    fold_high_severity_into_spec,
    review,
)
from src.agents.implementation import map_data, verify_mapping
from src.agents.validation import compare_results_to_claims
from src.data import DefeatBetaYahooSource, PointInTimeDataStore
from src.engine import run_backtest
from src.pdf.parser import parse_pdf
from src.report import format_data_quality_flags, format_replication_gap_line
from src.specs import PaperClaim, SupportingQuote

JT_PDF = Path("data/papers/jegadeesh_titman_1993_momentum.pdf")
OUT = Path("outputs/jt_full_pipeline.json")

# Paper's headline claim (JT 1993 Table I Panel A J=6 K=6 Buy-sell row).
JT_HEADLINE_CLAIM = PaperClaim(
    claim_id="jt_hl_66_monthly",
    metric="long_short_monthly_return",
    claimed_value=0.0095,
    claimed_tstat=3.07,
    claimed_units="fraction_per_month",
    paper_location="Table I Panel A, J=6 K=6 'Buy-sell' row",
    supporting_quote=SupportingQuote(text="Buy-sell 0.0095", page=7),
)


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    print("Parsing JT PDF (28 pages)...", flush=True)
    pdf = parse_pdf(JT_PDF)

    # --- A1 + A2 (cached) ---------------------------------------------
    section("A1 + A2 — Methodology extraction and verification")
    print("Running extract_and_verify (A1 extract -> A2 verify, up to 3 A1 retries)...", flush=True)
    verified = extract_and_verify(pdf, max_retries=3, use_cache=True)
    spec = verified.spec
    report = verified.report
    print(f"overall_confidence: {report.overall_confidence}")
    print(f"n_checks: {report.n_checks}  failed_high: {report.n_failed_high}  "
          f"failed_medium: {report.n_failed_medium}  retries: {report.retry_count}")
    for c in report.checks:
        sup = c.support_check.supports if c.support_check else "skipped"
        tag = "PASS" if not c.failed else "FAIL"
        print(f"  [{tag}] {c.field_path:10s} [{c.severity:6s}] "
              f"{c.verification_status:22s} page={c.verified_page}  supports={sup}")

    # --- A3 ----------------------------------------------------------
    section("A3 — Adversarial Reviewer")
    print("Running A3 (Opus, 3 criticisms)...", flush=True)
    critique = review(pdf, spec, verification_report=report, use_cache=True)
    for i, crit in enumerate(critique.criticisms, 1):
        print(f"\n  Criticism #{i} — [{crit.severity}] category={crit.category}")
        print(f"    description: {crit.description[:200]}")
        print(f"    remediation: {crit.proposed_remediation[:200]}")
        if crit.evidence_quote is not None:
            print(f"    evidence p.{crit.evidence_quote.page}: "
                  f"{crit.evidence_quote.text[:150]!r}")
        else:
            print(f"    evidence: (none — criticism about an absence)")

    # Fold high-severity criticisms into the spec's ambiguity list.
    n_high = sum(1 for c in critique.criticisms if c.severity == "high")
    spec_with_critique = fold_high_severity_into_spec(spec, critique)
    print(f"\n  {n_high} high-severity criticism(s) folded into spec.ambiguities "
          f"({len(spec.ambiguities)} -> {len(spec_with_critique.ambiguities)}).")

    # --- B1 + B2 ---------------------------------------------------
    section("B1 + B2 — Data mapping")
    print("Running B1 (Opus: spec + catalog -> DataMapping)...", flush=True)
    mapping = map_data(spec_with_critique, use_cache=True)
    print("Running B2 (Haiku + deterministic checks)...", flush=True)
    verified_mapping = verify_mapping(
        mapping, spec_with_critique, use_llm_check=True, use_cache=True
    )
    print(f"overall_fidelity: {verified_mapping.overall_fidelity}")
    print(f"blocking_issues:  {len(verified_mapping.blocking_issues)}")
    for check in verified_mapping.checks:
        m = check.field_mapping
        tag = "BLOCK" if check.blocking else "OK"
        print(f"  [{tag}] {m.spec_field:28s} -> {m.source_name}.{m.method} "
              f"[{m.fidelity:6s}] llm={check.llm_reasonable}")
        if m.fidelity_notes:
            print(f"         notes: {m.fidelity_notes[:160]}")

    # --- ENGINE ---------------------------------------------------
    section("Engine — backtest on clipped date range")
    clipped_spec = spec_with_critique.model_copy(update={
        "start_date": date(1995, 1, 1),
        "end_date": date(2020, 12, 31),
    })
    print(f"sample clipped to {clipped_spec.start_date} .. {clipped_spec.end_date} "
          "(defeatbeta coverage)")

    src = DefeatBetaYahooSource(cache_root=Path("data/cache/hf_datasets"))
    store = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": src},
        cache_dir=Path("data/cache/query_cache"),
    )
    print("Running backtest...", flush=True)
    result = run_backtest(clipped_spec, store, transaction_cost_bps=0.0)

    print(f"n_periods:         {result.n_periods}")
    print(f"mean monthly ret:  {result.mean_return*100:+.3f}%  ann. {result.annualized_return*100:+.2f}%")
    print(f"t-stat (NW lag {result.newey_west_lag}): {result.alpha_tstat:.2f}")
    print(f"Sharpe:            {result.sharpe_ratio:.2f}  "
          f"max_drawdown: {result.max_drawdown*100:.1f}%")

    # --- D1 ------------------------------------------------------
    section("D1 — Result Comparator (deterministic)")
    claims = [JT_HEADLINE_CLAIM]
    comparisons = compare_results_to_claims(result, claims)
    for comp in comparisons:
        print(f"  verdict: {comp.verdict}")
        print(f"  " + format_replication_gap_line(comp))
        if comp.tstat_gap is not None:
            print(f"  t-stat gap: paper {comp.claim.claimed_tstat:+.2f}  "
                  f"ours {comp.tstat_gap + comp.claim.claimed_tstat:+.2f}  "
                  f"(delta {comp.tstat_gap:+.2f})")

    # --- DATA QUALITY ------------------------------------------
    section("Data quality flags (translated)")
    for note in format_data_quality_flags(result):
        print(f"  - {note}")

    # --- SAVE ----------------------------------------------------
    OUT.write_text(json.dumps({
        "a2": {
            "overall_confidence": report.overall_confidence,
            "n_failed_high": report.n_failed_high,
            "retry_count": report.retry_count,
        },
        "a3": {
            "criticisms": [c.model_dump(mode="json") for c in critique.criticisms],
            "n_high_folded": n_high,
        },
        "b1_b2": {
            "overall_fidelity": verified_mapping.overall_fidelity,
            "n_blocking_issues": len(verified_mapping.blocking_issues),
            "mappings": [
                {
                    "field": c.field_mapping.spec_field,
                    "source": c.field_mapping.source_name,
                    "method": c.field_mapping.method,
                    "fidelity": c.field_mapping.fidelity,
                    "notes": c.field_mapping.fidelity_notes,
                    "llm_reasonable": c.llm_reasonable,
                    "blocking": c.blocking,
                }
                for c in verified_mapping.checks
            ],
        },
        "engine": {
            "mean_return": round(result.mean_return, 6),
            "t_stat": round(result.alpha_tstat, 3),
            "sharpe": round(result.sharpe_ratio, 3),
            "n_periods": result.n_periods,
        },
        "d1": [
            {
                "metric": c.claim.metric,
                "claimed": c.claim.claimed_value,
                "replicated": c.replicated_value,
                "gap": round(c.absolute_gap, 6),
                "verdict": c.verdict,
                "tstat_gap": round(c.tstat_gap, 3) if c.tstat_gap is not None else None,
            }
            for c in comparisons
        ],
        "data_quality": format_data_quality_flags(result),
    }, indent=2, default=str))
    section(f"wrote {OUT}")

    src.close()


if __name__ == "__main__":
    main()
