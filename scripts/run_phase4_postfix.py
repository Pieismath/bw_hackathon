"""Run the Phase 4 battery on the POST-D2-diagnosis spec.

D2 surfaced that JT's `signal.direction` should be `long_low` rather than
`long_high` for our 1995-2020 sample (Q2 fix to the engine made this
actually flip the sign). This script:

  1. Loads cached A1/A2/A3 → ReplicationSpec.
  2. Mutates spec.signal.direction → "long_low" (D2's primary cause).
  3. Runs the full battery on the fixed spec.
  4. Runs D3 on the new scorecard.
  5. Saves canonical outputs to:
       outputs/jt_robustness_scorecard.json
       outputs/jt_robustness_judgment.json

The pre-fix versions live at *_prefix.json — kept so the demo can show
the diagnosis-then-stress-test arc.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.agents.extraction import extract_and_verify, fold_high_severity_into_spec, review
from src.agents.validation import diagnose, judge
from src.agents.validation.divergence_diagnostician import (
    apply_mutation,
    run_backtest_cached,
)
from src.data import DefeatBetaYahooSource, PointInTimeDataStore
from src.pdf.parser import parse_pdf
from src.robustness import run_battery
from src.specs import PaperClaim, SupportingQuote

JT_PDF = Path("data/papers/jegadeesh_titman_1993_momentum.pdf")
SCORECARD_OUT = Path("outputs/jt_robustness_scorecard.json")
JUDGMENT_OUT = Path("outputs/jt_robustness_judgment.json")

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
    print("=" * 72, flush=True)


def main() -> None:
    SCORECARD_OUT.parent.mkdir(parents=True, exist_ok=True)

    print("Parsing JT PDF + loading A1/A2/A3 (cached)...", flush=True)
    pdf = parse_pdf(JT_PDF)
    verified = extract_and_verify(pdf, use_cache=True)
    spec = verified.spec
    critique = review(pdf, spec, verification_report=verified.report, use_cache=True)
    spec_with_critique = fold_high_severity_into_spec(spec, critique)
    clipped = spec_with_critique.model_copy(update={
        "start_date": date(1995, 1, 1),
        "end_date": date(2020, 12, 31),
    })

    # Apply D2's primary-cause fix
    fixed_spec = apply_mutation(clipped, "signal.direction", "long_low")
    section("POST-D2-FIX SPEC")
    print(f"  signal.direction: {clipped.signal.direction} -> {fixed_spec.signal.direction}")

    src = DefeatBetaYahooSource(cache_root=Path("data/cache/hf_datasets"))
    store = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": src},
        cache_dir=Path("data/cache/query_cache"),
    )

    section("BASELINE (post-fix)")
    baseline = run_backtest_cached(fixed_spec, store, transaction_cost_bps=0.0)
    print(f"  baseline mean: {baseline.mean_return*100:+.3f}%/mo  "
          f"t={baseline.alpha_tstat:.2f}  n={baseline.n_periods}")
    print(f"  data_quality_flags: {list(baseline.data_quality_flags)}")

    section("D2 DIAGNOSIS (cached, on post-fix baseline)")
    # D2 was originally run on the pre-fix spec; the cached output reflects that.
    # We pass it through here for D3's context; it remains valid as historical record.
    diagnosis_pre_fix = diagnose(
        spec=clipped, baseline_result=run_backtest_cached(clipped, store, 0.0),
        claim=JT_HEADLINE_CLAIM, store=store, use_cache=True,
    )
    print(f"  primary_cause: {diagnosis_pre_fix.primary_cause}")
    print(f"  primary_cause_kind: {diagnosis_pre_fix.primary_cause_kind}")
    print(f"  residual |gap|: {diagnosis_pre_fix.residual_abs_gap*100:.3f}%/mo")

    section("RUNNING ROBUSTNESS BATTERY (post-fix spec, ~10 min cached)")
    print("Families: lag, costs, subperiod, liquidity, data_quality, capacity",
          flush=True)
    scorecard = run_battery(fixed_spec, store)

    print()
    print(f"  baseline:           {scorecard.baseline_mean_return*100:+.3f}%/mo  "
          f"t={scorecard.baseline_tstat:.2f}")
    print(f"  n_tests:            {scorecard.n_tests}")
    print(f"  n_surviving:        {scorecard.n_surviving}")
    print(f"  cost_threshold_bps: {scorecard.cost_threshold_bps}")
    print(f"  lag_half_life_days: {scorecard.lag_half_life_days}")
    if scorecard.capacity_estimate_usd:
        print(f"  capacity_estimate:  ${scorecard.capacity_estimate_usd:,.0f}")
    else:
        print(f"  capacity_estimate:  n/a")
    print()
    print("  fragility_signals:")
    for s in scorecard.fragility_signals:
        print(f"    - {s}")

    print()
    print("  per-test results:")
    for t in scorecard.tests:
        tag = "✓" if t.surviving else " "
        ret = t.headline_metric
        ts = t.headline_tstat
        ts_s = f"t={ts:+.2f}" if ts is not None else "t=  n/a"
        print(f"    [{tag}] {t.name:36s} {t.family:11s} "
              f"ret={ret*100:+.3f}%  {ts_s}  n={t.n_periods}")

    SCORECARD_OUT.write_text(scorecard.model_dump_json(indent=2))
    print(f"\nwrote {SCORECARD_OUT}")

    section("D3 — Robustness Adversary on post-fix scorecard")
    print("Running D3 (cached if same inputs)...", flush=True)
    judgment = judge(
        scorecard=scorecard,
        baseline=baseline,
        claim=JT_HEADLINE_CLAIM,
        diagnosis=diagnosis_pre_fix,  # D2's pre-fix diagnosis is the relevant context
        use_cache=True,
    )
    print()
    print(f"  surviving: {judgment.surviving_count}/{judgment.n_tests}")
    print(f"  signal_type: {judgment.signal_type}")
    print(f"  implementable_alpha: {judgment.implementable_alpha*100:+.3f}%/mo")
    print(f"  basis: {judgment.implementable_alpha_basis}")
    if judgment.capacity_estimate_usd:
        print(f"  capacity_estimate_usd: ${judgment.capacity_estimate_usd:,.0f}")
    print(f"  gap_attribution: {judgment.gap_attribution}")
    print(f"    evidence: {judgment.gap_attribution_evidence}")
    print(f"  confidence: {judgment.confidence}")
    print()
    print("  primary_failure_modes:")
    for m in judgment.primary_failure_modes:
        print(f"    - {m}")
    print()
    print("  summary:")
    for line in judgment.summary.split("\n"):
        print(f"    {line}")

    JUDGMENT_OUT.write_text(judgment.model_dump_json(indent=2))
    print(f"\nwrote {JUDGMENT_OUT}")

    src.close()


if __name__ == "__main__":
    main()
