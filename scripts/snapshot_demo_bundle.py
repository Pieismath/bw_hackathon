"""Capture a live pipeline run on JT-1993 and freeze it as the /api/demo bundle.

Why: the hardcoded build_demo_bundle() in app/demo_fixture.py returns
plausible-but-fake JT numbers from before the engine and the post-A1
override existed. After the user runs JT through the live UI, the cached
numbers are realistic (μ=0.571%/mo, t=1.54, 370 periods, 0/11 robustness
survive, etc.). This script captures one such live run and writes it to
outputs/demo_bundle_live.json.

build_demo_bundle() is patched (in this same change) to read that file
if present, so /api/demo (and the "Load demo" button) returns the real
numbers instead of the fixture.

Run sequentially — the LLM cache, backtest cache, and battery cache
should all hit on a freshly-run JT, so wall time is <30 s.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.agents.extraction import extract_and_verify
from src.agents.extraction.adversarial_reviewer import review
from src.agents.validation import (
    diagnose,
    judge,
    run_backtest_cached,
)
from src.data import DefeatBetaYahooSource, PointInTimeDataStore
from src.engine import run_backtest
from src.factor_compare import compare_to_kf_factor
from src.pdf.parser import parse_pdf
from src.robustness import run_battery
from src.specs import PaperClaim, SupportingQuote


JT_PDF = Path("data/papers/jegadeesh_titman_1993_momentum.pdf")
OUT = Path("outputs/demo_bundle_live.json")
CACHE_ROOT = Path("data/cache/hf_datasets")


def _clip_window(spec):
    """Same logic as app.main._clip_spec_to_data_window — substitute pre-1994
    paper window with the available panel range."""
    import duckdb
    snap = next(
        (CACHE_ROOT / "defeatbeta" / "datasets--defeatbeta--yahoo-finance-data" / "snapshots").iterdir()
    )
    parquet = snap / "data" / "stock_prices.parquet"
    con = duckdb.connect(":memory:")
    row = con.execute(
        f"SELECT MIN(report_date), MAX(report_date) FROM read_parquet('{parquet}')"
    ).fetchone()
    con.close()
    data_min = date.fromisoformat(row[0])
    data_max = date.fromisoformat(row[1])
    info = {
        "paper_start": spec.start_date.isoformat(),
        "paper_end": spec.end_date.isoformat(),
        "data_panel_start": data_min.isoformat(),
        "data_panel_end": data_max.isoformat(),
        "engine_start": spec.start_date.isoformat(),
        "engine_end": spec.end_date.isoformat(),
        "substituted": False,
        "clipped": False,
        "overlap_kind": "in_range",
        "message": "",
    }
    if spec.start_date >= data_min and spec.end_date <= data_max:
        return spec, None, info
    if spec.end_date < data_min or spec.start_date > data_max:
        sub_start = date(max(data_min.year, 1995), 1, 1)
        info.update(
            engine_start=sub_start.isoformat(),
            engine_end=data_max.isoformat(),
            substituted=True,
            overlap_kind="no_overlap_post_publication_oos",
            message=f"original spec window {spec.start_date} → {spec.end_date} does not overlap data panel {data_min} → {data_max}; substituting {sub_start} → {data_max}",
        )
        return spec.model_copy(update={"start_date": sub_start, "end_date": data_max}), info["message"], info
    new_start = max(spec.start_date, data_min)
    new_end = min(spec.end_date, data_max)
    info.update(
        engine_start=new_start.isoformat(),
        engine_end=new_end.isoformat(),
        clipped=True,
        overlap_kind="partial_overlap_clipped",
        message=f"clipped from {spec.start_date}→{spec.end_date} to {new_start}→{new_end}",
    )
    return spec.model_copy(update={"start_date": new_start, "end_date": new_end}), info["message"], info


def main():
    print("Parsing JT PDF...")
    pdf = parse_pdf(JT_PDF)

    print("A1 + A2 (cached)...")
    verified = extract_and_verify(pdf, max_retries=3, use_cache=True, use_llm_check=True)

    print("A3 critique (cached)...")
    critique_obj = review(pdf, verified.spec, verified.report, use_cache=True)

    print("Engine backtest (cached)...")
    src = DefeatBetaYahooSource(cache_root=CACHE_ROOT)
    store = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": src},
        cache_dir=Path("data/cache/query_cache"),
    )
    spec_for_engine, clip_note, window_info = _clip_window(verified.spec)
    backtest = run_backtest(spec_for_engine, store, transaction_cost_bps=0.0)

    print("Robustness battery (cached)...")
    scorecard = run_battery(spec_for_engine, store, families=None)
    baseline = run_backtest_cached(spec_for_engine, store, transaction_cost_bps=0.0)
    placeholder_claim = PaperClaim(
        claim_id="placeholder_headline",
        metric="monthly_long_short_return",
        claimed_value=baseline.mean_return,
        claimed_tstat=baseline.alpha_tstat,
        claimed_units="decimal_per_month",
        paper_location="caller_did_not_supply_claim",
        supporting_quote=SupportingQuote(
            text="placeholder claim",
            page=1, verified=False, match_confidence=0.0,
        ),
    )
    judgment = judge(scorecard, baseline, placeholder_claim)

    print("D2 diagnose (cached)...")
    if verified.spec.headline_claim:
        hc = verified.spec.headline_claim
        d2_claim = PaperClaim(
            claim_id="paper_headline",
            metric=hc.metric,
            claimed_value=hc.monthly_return,
            claimed_tstat=hc.t_stat,
            claimed_units="decimal_per_month",
            paper_location=hc.paper_location,
            supporting_quote=hc.supporting_quote,
        )
    else:
        d2_claim = placeholder_claim
    diagnosis = diagnose(
        spec=spec_for_engine,
        baseline_result=baseline,
        claim=d2_claim,
        store=store,
        max_experiments=3,
        transaction_cost_bps=0.0,
    )

    print("Factor compare (KF MOM)...")
    factor_compare = compare_to_kf_factor(verified.spec, verified.spec.headline_claim)

    for s in store.sources.values():
        try: s.close()
        except AttributeError: pass

    bundle = {
        "paper_id": verified.spec.paper_id,
        "paper_title": verified.spec.paper_title,
        "verified_spec": verified.model_dump(mode="json"),
        "critique": critique_obj.model_dump(mode="json"),
        "backtest": backtest.model_dump(mode="json"),
        "paper_claim": (
            {
                "monthly_return": verified.spec.headline_claim.monthly_return,
                "tstat": verified.spec.headline_claim.t_stat,
                "window": verified.spec.headline_claim.window_label,
                "paper_location": verified.spec.headline_claim.paper_location,
            }
            if verified.spec.headline_claim else None
        ),
        "robustness": {
            "scorecard": scorecard.model_dump(mode="json"),
            "judgment": judgment.model_dump(mode="json"),
        },
        "diagnosis": {
            "diagnosis": diagnosis.model_dump(mode="json"),
            "n_experiments": diagnosis.experiments_run,
        },
        "factor_compare": factor_compare.model_dump(mode="json"),
        "window_info": window_info,
        "clip_note": clip_note,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bundle, indent=2, default=str))
    bt = backtest
    print()
    print(f"wrote {OUT}")
    print(f"  paper_id      : {bundle['paper_id']}")
    print(f"  engine window : {bt.start_date} → {bt.end_date} (n={bt.n_periods})")
    print(f"  mean_return   : {bt.mean_return * 100:+.3f}%/mo  t={bt.alpha_tstat:.2f}  Sharpe={bt.sharpe_ratio:.2f}")
    print(f"  D3 verdict    : {judgment.surviving_count}/{judgment.n_tests} surviving · {judgment.signal_type}")
    print(f"  D2 primary    : {diagnosis.primary_cause}")
    print(f"  KF compare    : {factor_compare.verdict}")


if __name__ == "__main__":
    main()
