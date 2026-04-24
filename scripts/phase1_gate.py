"""Phase 1 completion gate.

Runs three momentum backtests against the canonical US equity universe and
validates the value-weighted KF-convention variant against Ken French's
published MOM factor series. This is the single most important structural
check in Phase 1 — if the engine can't reproduce a widely-known factor
series with >0.85 correlation on overlapping months, downstream agents
would amplify that error.

Kept permanent as a regression harness. Re-run any time the data store,
engine, or panel logic changes:

    .venv/bin/python -u scripts/phase1_gate.py

Also writes outputs/phase1_validation.json for programmatic reuse.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from src.data import DefeatBetaYahooSource, PointInTimeDataStore
from src.data.ken_french import load_kf_mom_monthly
from src.engine import run_backtest
from src.specs import (
    PortfolioSpec,
    RebalanceSpec,
    ReplicationSpec,
    SignalSpec,
    UniverseSpec,
)


START, END = date(2000, 1, 1), date(2023, 12, 31)
KF_CSV = Path("data/cache/ken_french/F-F_Momentum_Factor.csv")
OUT_JSON = Path("outputs/phase1_validation.json")


def build_spec(lookback: int, skip: int, holding: int, weighting: str) -> ReplicationSpec:
    return ReplicationSpec(
        paper_id="momentum",
        paper_title=f"({lookback},{skip},{holding})_{weighting}",
        universe=UniverseSpec(name="us_common", min_price=5.0),
        signal=SignalSpec(
            name="past_return",
            formula=f"p(t-{skip}) / p(t-{skip+lookback}) - 1",
            inputs=("close",),
            kind="past_return",
            lookback_months=lookback,
            skip_months=skip,
            frequency="monthly",
            direction="long_high",
        ),
        portfolio=PortfolioSpec(
            construction="decile",
            n_buckets=10,
            long_bucket=10,
            short_bucket=1,
            weighting=weighting,
            long_short=True,
            gross_exposure=2.0,
        ),
        rebalance=RebalanceSpec(
            frequency="monthly",
            execution_lag_days=1,
            holding_period_months=holding,
        ),
        start_date=START,
        end_date=END,
    )


def print_result(label: str, r) -> None:
    print(f"  n_periods:          {r.n_periods}")
    print(f"  mean monthly ret:   {r.mean_return*100:+.3f}%  (ann. {r.annualized_return*100:+.2f}%)")
    print(f"  vol annualized:     {r.volatility*100:.2f}%")
    print(f"  Sharpe:             {r.sharpe_ratio:.2f}")
    print(f"  t-stat (NW lag {r.newey_west_lag}): {r.alpha_tstat:.2f}")
    print(f"  max drawdown:       {r.max_drawdown*100:.1f}%  |  hit {r.hit_rate*100:.1f}%")
    if r.data_quality_flags:
        print(f"  data_quality_flags: {r.data_quality_flags}")


def as_series(r) -> pd.Series:
    return pd.Series(
        {pd.Timestamp(o.period_end): o.ret for o in r.returns}
    ).sort_index()


def main() -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    print("Loading store + KF MOM...", flush=True)
    src = DefeatBetaYahooSource(cache_root=Path("data/cache/hf_datasets"))
    store = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": src},
        cache_dir=Path("data/cache/query_cache"),
    )
    kf = load_kf_mom_monthly(KF_CSV)

    runs: dict[str, object] = {}

    print("\n" + "=" * 72)
    print("RUN 1: JT-canonical (6,1,6) decile equal-weighted, 2000-2023")
    print("=" * 72, flush=True)
    print("Running (6,1,6) EW ...", flush=True)
    r_jt = run_backtest(
        build_spec(6, 1, 6, "equal"), store, transaction_cost_bps=0.0
    )
    print_result("JT (6,1,6) EW", r_jt)
    runs["(6,1,6) EW JT"] = r_jt

    print("\n" + "=" * 72)
    print("RUN 2: KF convention (11,1,1) decile value-weighted, 2000-2023")
    print("=" * 72, flush=True)
    print("Running (11,1,1) VW ...", flush=True)
    r_vw = run_backtest(
        build_spec(11, 1, 1, "value"), store, transaction_cost_bps=0.0
    )
    print_result("KF (11,1,1) VW", r_vw)
    runs["(11,1,1) VW"] = r_vw

    print("\n" + "=" * 72)
    print("RUN 3: KF convention (11,1,1) decile equal-weighted (weighting ablation)")
    print("=" * 72, flush=True)
    print("Running (11,1,1) EW ...", flush=True)
    r_ew = run_backtest(
        build_spec(11, 1, 1, "equal"), store, transaction_cost_bps=0.0
    )
    print_result("KF (11,1,1) EW", r_ew)
    runs["(11,1,1) EW"] = r_ew

    print("\n" + "=" * 72)
    print("VALIDATION: correlation vs Ken French MOM")
    print("=" * 72, flush=True)
    summary: dict[str, dict] = {}
    for name, r in runs.items():
        s = as_series(r)
        common = s.index.intersection(kf.index)
        if len(common) <= 10:
            print(f"  {name:18s}: overlap={len(common)} — too short to correlate")
            summary[name] = {"n_periods": r.n_periods, "overlap": len(common), "corr": None}
            continue
        corr = float(s.loc[common].corr(kf.loc[common]))
        ours_mean = s.loc[common].mean() * 10000
        ours_std = s.loc[common].std() * 10000
        kf_mean = kf.loc[common].mean() * 10000
        kf_std = kf.loc[common].std() * 10000
        print(
            f"  {name:18s}: overlap={len(common):3d}  corr={corr:+.3f}  "
            f"ours {ours_mean:+6.0f}bps±{ours_std:5.0f}bps  "
            f"KF {kf_mean:+6.0f}bps±{kf_std:5.0f}bps"
        )
        summary[name] = {
            "n_periods": r.n_periods,
            "overlap": len(common),
            "corr": round(corr, 4),
            "mean_bps": round(ours_mean, 1),
            "std_bps": round(ours_std, 1),
            "kf_mean_bps": round(kf_mean, 1),
            "kf_std_bps": round(kf_std, 1),
        }

    print()
    vw_corr = summary.get("(11,1,1) VW", {}).get("corr") or 0.0
    gate = "PASS" if vw_corr > 0.85 else ("CLOSE" if vw_corr > 0.70 else "FAIL")
    print(
        f"GATE — VW (11,1,1) vs KF MOM > 0.85 required: got {vw_corr:+.3f}  [{gate}]"
    )

    with OUT_JSON.open("w") as f:
        json.dump(
            {"gate": gate, "vw_corr": vw_corr, "runs": summary},
            f,
            indent=2,
        )
    print(f"\nwrote {OUT_JSON}")

    src.close()


if __name__ == "__main__":
    main()
