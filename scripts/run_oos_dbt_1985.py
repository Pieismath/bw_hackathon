"""Out-of-sample run: De Bondt & Thaler 1985 ("Does the Stock Market Overreact?").

Tests whether the system generalizes beyond JT's tuning. The key
architectural test: A1 must extract `signal.direction = "long_low"` and
a multi-year holding period from the paper's prose alone — it has not
been tuned for this paper.

Pre-emptive issues we expect to surface (already noted in LIMITATIONS.md
once DBT's findings are folded in):
  - Sample window 1926-1982 vs defeatbeta_yahoo's 1994+: B2 should hard-
    block on data window mismatch. Engine run is performed on a
    substituted 1995-2020 window with the substitution flagged loudly
    in data_quality_flags.
  - 3-5 year holding period: tests the engine's monthly tranche logic
    over much longer horizons than JT.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from src.agents.extraction import (
    extract_and_verify,
    fold_high_severity_into_spec,
    review,
)
from src.agents.implementation import map_data, verify_mapping
from src.agents.validation import compare_results_to_claims, diagnose, judge
from src.agents.validation.divergence_diagnostician import run_backtest_cached
from src.data import DefeatBetaYahooSource, PointInTimeDataStore
from src.pdf.parser import parse_pdf
from src.robustness import run_battery
from src.specs import PaperClaim, SupportingQuote

DBT_PDF = Path("data/papers/debondt_thaler_1985_overreaction.pdf")
SCORECARD_OUT = Path("outputs/dbt_robustness_scorecard.json")
JUDGMENT_OUT = Path("outputs/dbt_robustness_judgment.json")
SPEC_OUT = Path("outputs/dbt_a1_extraction.json")
PIPELINE_OUT = Path("outputs/dbt_pipeline.json")

# DBT 1985 headline result: Table I shows the 36-month CAPM-adjusted excess
# return for the loser-minus-winner portfolio over 36-month formation. The
# paper reports ~25% cumulative arbitrage profit over 3 years (~0.65%/mo).
# We use a monthly-equivalent claim for D1 / D2 compatibility with the JT
# pipeline. Acknowledged: DBT's headline is in CAR space, not monthly-mean
# space — there is a units mismatch the report should flag.
DBT_HEADLINE_CLAIM = PaperClaim(
    claim_id="dbt_loser_minus_winner_monthly_equivalent",
    metric="long_short_monthly_return",
    claimed_value=0.0067,           # ~25% over 36 months → ~0.67%/mo
    claimed_tstat=None,
    claimed_units="fraction_per_month_equivalent_of_36mo_CAR",
    paper_location="Table I, 36-month formation, loser-winner",
    supporting_quote=SupportingQuote(
        text="loser portfolios outperform the market", page=4,
    ),
)


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72, flush=True)


def main() -> None:
    SCORECARD_OUT.parent.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(
        description="Out-of-sample DBT 1985 pipeline. Families filter and "
                    "D2 budget keep wall time tractable on long-K specs."
    )
    parser.add_argument(
        "--families",
        default="costs,liquidity,capacity,data_quality",
        help="Comma-separated battery families to run. Default skips 'lag' "
             "(monthly-granularity engine makes it uninformative) and "
             "'subperiod' (11 reruns × 4-min DBT engine = too slow).",
    )
    parser.add_argument(
        "--d2-max-experiments",
        type=int,
        default=3,
        help="Cap on D2 mutation experiments. Default 3 (down from 6) keeps "
             "OOS wall time bounded on long-K specs.",
    )
    args = parser.parse_args()
    families = tuple(f.strip() for f in args.families.split(",") if f.strip())
    d2_budget = args.d2_max_experiments
    print(f"Battery families:     {list(families)}")
    print(f"D2 max experiments:   {d2_budget}")

    section("PARSING DBT 1985")
    pdf = parse_pdf(DBT_PDF)
    print(f"  pages: {pdf.n_pages}")

    section("A1 + A2 — Extraction + verification")
    print("Running A1 + A2 (Opus + Haiku, ~30-60s on first call)...", flush=True)
    verified = extract_and_verify(pdf, max_retries=3, use_cache=True)
    spec = verified.spec
    report = verified.report

    print(f"  paper_id:    {spec.paper_id}")
    print(f"  paper_title: {spec.paper_title[:80]}")
    print(f"  sample:      {spec.start_date} → {spec.end_date}")
    print(f"  signal:      kind={spec.signal.kind}  lookback={spec.signal.lookback_months}m  "
          f"skip={spec.signal.skip_months}m  direction={spec.signal.direction}")
    print(f"  portfolio:   {spec.portfolio.construction} n={spec.portfolio.n_buckets}  "
          f"long={spec.portfolio.long_bucket} short={spec.portfolio.short_bucket}  "
          f"weighting={spec.portfolio.weighting}")
    print(f"  rebalance:   freq={spec.rebalance.frequency}  "
          f"holding={spec.rebalance.holding_period_months}m  "
          f"exec_lag={spec.rebalance.execution_lag_days}d")
    SPEC_OUT.write_text(spec.model_dump_json(indent=2))

    print()
    print(f"  A2 confidence: {report.overall_confidence}  "
          f"failed_high: {report.n_failed_high}  retries: {report.retry_count}")
    for c in report.checks:
        sup = c.support_check.supports if c.support_check else "skipped"
        print(f"    [{'PASS' if not c.failed else 'FAIL'}] {c.field_path:10s} "
              f"[{c.severity:6s}] {c.verification_status:22s} "
              f"page={c.verified_page}  supports={sup}")

    print()
    print(f"  ambiguities: {len(spec.ambiguities)}")
    for a in spec.ambiguities[:8]:
        print(f"    [{a.sensitivity_priority:6s}] {a.parameter}: "
              f"default={str(a.default_chosen)[:50]}")
    if len(spec.ambiguities) > 8:
        print(f"    ... and {len(spec.ambiguities) - 8} more")

    section("A3 — Adversarial review")
    print("Running A3 (Opus)...", flush=True)
    critique = review(pdf, spec, verification_report=report, use_cache=True)
    for i, crit in enumerate(critique.criticisms, 1):
        print(f"\n  #{i} [{crit.severity}] {crit.category}")
        print(f"    {crit.description[:200]}")
    spec_with_critique = fold_high_severity_into_spec(spec, critique)
    n_high = sum(1 for c in critique.criticisms if c.severity == "high")
    print(f"\n  {n_high} high-severity criticism(s) folded "
          f"({len(spec.ambiguities)} → {len(spec_with_critique.ambiguities)})")

    section("B1 + B2 — Data mapping (expect hard block on date window)")
    print("Running B1 (Opus) + B2 (Haiku) ...", flush=True)
    mapping = map_data(spec_with_critique, use_cache=True)
    verified_mapping = verify_mapping(
        mapping, spec_with_critique, use_llm_check=True, use_cache=True
    )
    print(f"  overall_fidelity: {verified_mapping.overall_fidelity}")
    print(f"  blocking_issues:  {len(verified_mapping.blocking_issues)}")
    for c in verified_mapping.checks:
        m = c.field_mapping
        tag = "BLOCK" if c.blocking else "OK"
        print(f"    [{tag}] {m.spec_field:28s} -> {m.source_name}.{m.method} "
              f"[{m.fidelity:6s}] llm={c.llm_reasonable}")

    section("DATE-WINDOW SUBSTITUTION")
    print(f"  paper sample:  {spec_with_critique.start_date} → "
          f"{spec_with_critique.end_date}")
    print(f"  defeatbeta:    1994-11 → 2026-04 (no overlap with paper window)")
    print(f"  substituting:  1995-01 → 2020-12 (engine run)")
    print(f"  This is a DATA-LIMITATION substitution. The result must be read "
          "as 'how the spec performs on substituted data', not as a replication "
          "of the paper's reported number.")
    clipped_spec = spec_with_critique.model_copy(update={
        "start_date": date(1995, 1, 1),
        "end_date": date(2020, 12, 31),
    })

    src = DefeatBetaYahooSource(cache_root=Path("data/cache/hf_datasets"))
    store = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": src},
        cache_dir=Path("data/cache/query_cache"),
    )

    section("ENGINE — backtest on substituted window")
    try:
        baseline = run_backtest_cached(clipped_spec, store, transaction_cost_bps=0.0)
    except Exception as e:
        print(f"  ENGINE FAILED: {e}")
        print("  Likely cause: holding period too long for monthly tranche logic, "
              "or signal lookback exceeds available history at start.")
        src.close()
        return
    print(f"  mean monthly:  {baseline.mean_return*100:+.3f}%/mo  "
          f"t={baseline.alpha_tstat:.2f}  n={baseline.n_periods}")
    print(f"  data_quality_flags:")
    for f in baseline.data_quality_flags:
        print(f"    - {f}")

    section("D1 + D2 — Compare and diagnose")
    comparisons = compare_results_to_claims(baseline, [DBT_HEADLINE_CLAIM])
    for c in comparisons:
        print(f"  D1 verdict: {c.verdict}  paper {c.claim.claimed_value*100:+.3f}%/mo  "
              f"ours {c.replicated_value*100:+.3f}%/mo  gap {c.absolute_gap*100:+.3f}%/mo")

    print("\nRunning D2 (Opus)...", flush=True)
    diagnosis = diagnose(
        spec=clipped_spec, baseline_result=baseline,
        claim=DBT_HEADLINE_CLAIM, store=store,
        max_experiments=d2_budget, use_cache=True,
    )
    print(f"  primary_cause: {diagnosis.primary_cause}")
    print(f"  primary_cause_kind: {diagnosis.primary_cause_kind}")
    print(f"  residual |gap|: {diagnosis.residual_abs_gap*100:.3f}%/mo")
    print(f"  experiments_run: {diagnosis.experiments_run}")
    print(f"  confidence: {diagnosis.confidence}")

    section(f"ROBUSTNESS BATTERY (OOS, families={list(families)})")
    print("Running battery...", flush=True)
    scorecard = run_battery(clipped_spec, store, families=families)
    print(f"  baseline:           {scorecard.baseline_mean_return*100:+.3f}%/mo  "
          f"t={scorecard.baseline_tstat:.2f}")
    print(f"  n_tests / surviving: {scorecard.n_tests} / {scorecard.n_surviving}")
    print(f"  cost_threshold_bps: {scorecard.cost_threshold_bps}")
    print(f"  lag_half_life_days: {scorecard.lag_half_life_days}")
    if scorecard.capacity_estimate_usd:
        print(f"  capacity_estimate:  ${scorecard.capacity_estimate_usd:,.0f}")
    SCORECARD_OUT.write_text(scorecard.model_dump_json(indent=2))

    section("D3 — Robustness Adversary on DBT scorecard")
    print("Running D3 (Opus, cached)...", flush=True)
    judgment = judge(
        scorecard=scorecard, baseline=baseline,
        claim=DBT_HEADLINE_CLAIM, diagnosis=diagnosis, use_cache=True,
    )
    print(f"  surviving:           {judgment.surviving_count}/{judgment.n_tests}")
    print(f"  signal_type:         {judgment.signal_type}")
    print(f"  implementable_alpha: {judgment.implementable_alpha*100:+.3f}%/mo")
    print(f"  basis:               {judgment.implementable_alpha_basis}")
    print(f"  gap_attribution:     {judgment.gap_attribution}")
    print(f"    evidence: {judgment.gap_attribution_evidence}")
    print(f"  confidence:          {judgment.confidence}")
    print()
    print("  primary_failure_modes:")
    for m in judgment.primary_failure_modes:
        print(f"    - {m}")
    print()
    print("  summary:")
    for line in judgment.summary.split("\n"):
        print(f"    {line}")
    JUDGMENT_OUT.write_text(judgment.model_dump_json(indent=2))

    PIPELINE_OUT.write_text(json.dumps({
        "paper_id": spec.paper_id,
        "paper_title": spec.paper_title,
        "extracted_signal_direction": spec.signal.direction,
        "extracted_lookback_months": spec.signal.lookback_months,
        "extracted_holding_months": spec.rebalance.holding_period_months,
        "a2_confidence": report.overall_confidence,
        "a3_n_high": n_high,
        "b1_b2_overall_fidelity": verified_mapping.overall_fidelity,
        "b1_b2_blocking_issues": list(verified_mapping.blocking_issues),
        "engine_substituted_dates": True,
        "engine_mean_return": baseline.mean_return,
        "engine_tstat": baseline.alpha_tstat,
        "d1_verdict": comparisons[0].verdict,
        "d2_primary_cause": diagnosis.primary_cause,
        "d2_primary_cause_kind": diagnosis.primary_cause_kind,
        "d2_residual_abs_gap": diagnosis.residual_abs_gap,
        "battery_surviving": scorecard.n_surviving,
        "battery_n_tests": scorecard.n_tests,
        "d3_implementable_alpha": judgment.implementable_alpha,
        "d3_signal_type": judgment.signal_type,
        "d3_gap_attribution": judgment.gap_attribution,
        "d3_confidence": judgment.confidence,
    }, indent=2, default=str))

    print()
    print(f"wrote {SPEC_OUT}, {SCORECARD_OUT}, {JUDGMENT_OUT}, {PIPELINE_OUT}")
    src.close()


if __name__ == "__main__":
    main()
