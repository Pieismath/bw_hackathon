"""Run D2 (Divergence Diagnostician) on the JT 1993 replication gap.

Depends on:
  - A1's cached extraction of the JT methodology spec.
  - A2's cached verification (for consistency with earlier steps).
  - A3's cached adversarial critique (folded into ambiguity list).
  - The backtest engine running on defeatbeta (cached data panels).

D2 then mutates one spec field at a time, reruns the engine (cached by
spec hash), and reports a structured diagnosis.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.agents.extraction import extract_and_verify, fold_high_severity_into_spec, review
from src.agents.validation import diagnose
from src.agents.validation.divergence_diagnostician import run_backtest_cached
from src.data import DefeatBetaYahooSource, PointInTimeDataStore
from src.pdf.parser import parse_pdf
from src.specs import PaperClaim, SupportingQuote

JT_PDF = Path("data/papers/jegadeesh_titman_1993_momentum.pdf")
OUT = Path("outputs/jt_d2_diagnosis.json")

JT_HEADLINE_CLAIM = PaperClaim(
    claim_id="jt_hl_66_monthly",
    metric="long_short_monthly_return",
    claimed_value=0.0095,
    claimed_tstat=3.07,
    claimed_units="fraction_per_month",
    paper_location="Table I Panel A, J=6 K=6 'Buy-sell' row",
    supporting_quote=SupportingQuote(text="Buy-sell 0.0095", page=7),
)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    print("Parsing JT PDF...", flush=True)
    pdf = parse_pdf(JT_PDF)

    print("Loading A1 + A2 (cached)...", flush=True)
    verified = extract_and_verify(pdf, use_cache=True)
    spec = verified.spec

    print("Loading A3 critique (cached) + folding high-severity into ambiguities...", flush=True)
    critique = review(pdf, spec, verification_report=verified.report, use_cache=True)
    spec_with_critique = fold_high_severity_into_spec(spec, critique)
    print(f"  spec.ambiguities: {len(spec.ambiguities)} -> {len(spec_with_critique.ambiguities)}")

    # Clip dates to defeatbeta's coverage so the engine can run
    clipped_spec = spec_with_critique.model_copy(update={
        "start_date": date(1995, 1, 1),
        "end_date": date(2020, 12, 31),
    })

    print("Opening store + running baseline backtest (cached)...", flush=True)
    src = DefeatBetaYahooSource(cache_root=Path("data/cache/hf_datasets"))
    store = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": src},
        cache_dir=Path("data/cache/query_cache"),
    )
    baseline_result = run_backtest_cached(clipped_spec, store, transaction_cost_bps=0.0)
    print(f"  baseline: mean={baseline_result.mean_return*100:+.3f}%/mo  "
          f"t={baseline_result.alpha_tstat:.2f}  n={baseline_result.n_periods}")

    print()
    print("=" * 72)
    print("D2 — Divergence Diagnostician")
    print("=" * 72)
    print("Running D2 (Opus proposer + engine reruns + Opus summarizer)...")
    print("Budget: up to 6 experiments. Each engine rerun ~30-90s on first call.")
    print(flush=True)

    diagnosis = diagnose(
        spec=clipped_spec,
        baseline_result=baseline_result,
        claim=JT_HEADLINE_CLAIM,
        store=store,
        max_experiments=6,
        use_cache=True,
        transaction_cost_bps=0.0,
    )

    print()
    print("=" * 72)
    print(f"EXPERIMENTS ({diagnosis.experiments_run} run)")
    print("=" * 72)
    for i, r in enumerate(diagnosis.mutation_results, 1):
        print(f"\n  #{i}  {r.proposal.parameter} : {r.from_value_human!r} -> {r.proposal.to_value!r}")
        print(f"      rationale: {r.proposal.rationale[:180]}")
        print(f"      pre  mean={r.pre_mean_return*100:+.3f}%/mo  |gap|={r.pre_abs_gap*100:.3f}%/mo  "
              f"verdict={r.verdict_before}")
        print(f"      post mean={r.post_mean_return*100:+.3f}%/mo  |gap|={r.post_abs_gap*100:.3f}%/mo  "
              f"verdict={r.verdict_after}")
        delta_sign = "CLOSED" if r.gap_delta > 0 else ("WIDENED" if r.gap_delta < 0 else "no change")
        print(f"      gap_delta: {r.gap_delta*100:+.3f}%/mo ({delta_sign})  "
              f"sign-flip-closed: {r.closed_sign_flip}")

    print()
    print("=" * 72)
    print("DIAGNOSIS")
    print("=" * 72)
    print(f"primary_cause:         {diagnosis.primary_cause}")
    print(f"evidence:              {diagnosis.primary_cause_evidence}")
    print(f"alternatives_tested:   {list(diagnosis.alternatives_tested)}")
    print(f"alternatives_ruled_out:{list(diagnosis.alternatives_ruled_out)}")
    print(f"residual |gap|:        {diagnosis.residual_abs_gap*100:.3f}%/mo")
    print(f"residual cause:        {diagnosis.residual_gap_likely_cause}")
    print(f"confidence:            {diagnosis.confidence}")
    print(f"early_exit:            {diagnosis.early_exit}  reason: {diagnosis.early_exit_reason}")

    OUT.write_text(diagnosis.model_dump_json(indent=2))
    print(f"\nwrote {OUT}")
    src.close()


if __name__ == "__main__":
    main()
