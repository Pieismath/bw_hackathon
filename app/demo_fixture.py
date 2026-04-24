"""Build a realistic demo bundle (spec + verification + critique + backtest).

The backend can surface this bundle via /api/demo so the UI renders with
meaningful data without needing the Anthropic API key or the parquet data
cache. Numbers here are plausible Jegadeesh-Titman 1993 replication figures
consistent with what Phase 2 produces end-to-end.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

from src.specs import (
    AdversarialCritique,
    AmbiguityFlag,
    BacktestResult,
    Criticism,
    PortfolioSpec,
    ProvenanceRecord,
    QuoteVerification,
    RebalanceSpec,
    ReplicationSpec,
    ReturnObservation,
    SignalSpec,
    SupportCheck,
    SupportingQuote,
    UniverseSpec,
    VerificationReport,
    VerifiedReplicationSpec,
)


def _build_spec() -> ReplicationSpec:
    return ReplicationSpec(
        paper_id="jegadeesh_titman_1993",
        paper_title="Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency",
        universe=UniverseSpec(
            name="NYSE/AMEX common stocks",
            region="US",
            asset_class="equity",
            supporting_quote=SupportingQuote(
                text="Our sample includes all stocks traded on the NYSE and AMEX.",
                page=5,
                verified=True,
                match_confidence=1.0,
            ),
        ),
        signal=SignalSpec(
            name="past 6-month return (skip 0)",
            formula="cumulative return from t-6 to t",
            inputs=("close",),
            direction="long_high",
            frequency="monthly",
            kind="past_return",
            lookback_months=6,
            skip_months=0,
            supporting_quote=SupportingQuote(
                text="We examine strategies that select stocks based on their returns over the past 3, 6, 9, and 12 months.",
                page=3,
                verified=True,
                match_confidence=1.0,
            ),
        ),
        portfolio=PortfolioSpec(
            construction="decile",
            n_buckets=10,
            long_bucket=10,
            short_bucket=1,
            weighting="equal",
            long_short=True,
            gross_exposure=2.0,
            supporting_quote=SupportingQuote(
                text="Stocks are ranked in ascending order on the basis of their returns and assigned to one of ten equal-weighted portfolios.",
                page=5,
                verified=True,
                match_confidence=1.0,
            ),
        ),
        rebalance=RebalanceSpec(
            frequency="monthly",
            execution_lag_days=1,
            holding_period_months=6,
            supporting_quote=SupportingQuote(
                text="The J-month/K-month strategy selects stocks based on their returns over the past J months and holds them for K months.",
                page=4,
                verified=True,
                match_confidence=0.97,
            ),
        ),
        start_date=date(1995, 1, 1),
        end_date=date(2020, 12, 31),
        ambiguities=(
            AmbiguityFlag(
                parameter="transaction_cost_bps",
                default_chosen="0",
                alternatives=("10", "20", "50"),
                sensitivity_priority="high",
                reason="Paper reports gross returns; real-world costs would materially affect the long-short spread.",
            ),
            AmbiguityFlag(
                parameter="universe.exchange_filter",
                default_chosen="defeatbeta_all_equities (US, no exchange filter)",
                alternatives=("NYSE-only", "NYSE+AMEX"),
                sensitivity_priority="medium",
                reason="Yahoo-sourced panel does not expose exchange tags; using the broad US universe as a substitute.",
            ),
            AmbiguityFlag(
                parameter="signal.skip_months",
                default_chosen="0",
                alternatives=("1",),
                sensitivity_priority="medium",
                reason="Original paper uses J/K with no explicit skip month for most tables. JT-adjacent literature adds a 1-month skip.",
            ),
        ),
        notes="Clipped to 1995–2020 because defeatbeta price panel begins 1994-11-30.",
    )


def _build_verification_report(spec: ReplicationSpec) -> VerificationReport:
    checks = (
        QuoteVerification(
            field_path="universe",
            quote=spec.universe.supporting_quote,
            severity="medium",
            verification_status="verified",
            verified_page=5,
            verification_confidence=1.0,
            support_check=SupportCheck(
                supports="yes",
                reason="Quote explicitly states the NYSE/AMEX universe used in the paper.",
            ),
            failed=False,
        ),
        QuoteVerification(
            field_path="signal",
            quote=spec.signal.supporting_quote,
            severity="high",
            verification_status="verified",
            verified_page=3,
            verification_confidence=1.0,
            support_check=SupportCheck(
                supports="yes",
                reason="Quote defines the J-month lookback signals directly.",
            ),
            failed=False,
        ),
        QuoteVerification(
            field_path="portfolio",
            quote=spec.portfolio.supporting_quote,
            severity="high",
            verification_status="verified",
            verified_page=5,
            verification_confidence=1.0,
            support_check=SupportCheck(
                supports="yes",
                reason="Quote defines the decile sort and equal-weighted construction.",
            ),
            failed=False,
        ),
        QuoteVerification(
            field_path="rebalance",
            quote=spec.rebalance.supporting_quote,
            severity="high",
            verification_status="fuzzy_match",
            verified_page=4,
            verification_confidence=0.91,
            support_check=SupportCheck(
                supports="partial",
                reason="Quote establishes J/K notation but does not state execution lag; lag is an ambiguity.",
            ),
            failed=False,
        ),
    )
    return VerificationReport(
        checks=checks,
        overall_confidence="high",
        n_checks=len(checks),
        n_failed_high=0,
        n_failed_medium=0,
        n_failed_low=0,
        retry_count=0,
    )


def _build_critique() -> AdversarialCritique:
    return AdversarialCritique(
        criticisms=[
            Criticism(
                category="missed",
                severity="high",
                description="The spec ignores the 1-month gap between formation and holding that several JT tables use. That skip changes the sign of some sub-period returns and should appear either as a signal.skip_months default of 1 or as an ambiguity with sensitivity_priority=high.",
                proposed_remediation="Set signal.skip_months=1 as the default and keep skip_months=0 as the alternative in the ambiguity list.",
            ),
            Criticism(
                category="oversimplified",
                severity="medium",
                description="Setting transaction_cost_bps=0 is defensible for a gross-return replication, but the paper's appendix discusses a 50-bps round-trip benchmark. Flagging cost as high-sensitivity is correct but the spec should run the 50-bps rerun inside the default sweep.",
                proposed_remediation="Add a robustness rerun at transaction_cost_bps=50 and surface the net-of-cost spread alongside gross.",
            ),
            Criticism(
                category="alternative_interpretation",
                severity="medium",
                description="The universe was set to all US equities because the data source lacks exchange tags. NYSE-only replication drops ~40% of names and is how JT originally ran the sort; that substitution should be surfaced in the report as a first-class caveat, not a footnote.",
                proposed_remediation="Promote the exchange substitution from a passing ambiguity to a top-level data_quality_flag on the BacktestResult.",
            ),
        ],
    )


def _build_backtest(spec: ReplicationSpec) -> BacktestResult:
    # Build a plausible monthly long-short return series (1995-01 → 2020-12).
    # Mean ≈ 45 bps/mo, vol ≈ 6%/mo, negative skew, momentum-crash dip in
    # 2009-Q2 to match the stylized literature. Deterministic.
    months: list[date] = []
    y, m = 1995, 1
    while (y, m) <= (2020, 12):
        # month-end
        if m == 12:
            nxt_y, nxt_m = y + 1, 1
        else:
            nxt_y, nxt_m = y, m + 1
        # last day of month m
        from calendar import monthrange
        last_day = monthrange(y, m)[1]
        months.append(date(y, m, last_day))
        y, m = nxt_y, nxt_m

    observations: list[ReturnObservation] = []
    rng_state = 1234567
    for i, d in enumerate(months):
        # Simple LCG for determinism (no numpy in fixture land).
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        noise = (rng_state / 0x7FFFFFFF) - 0.5  # [-0.5, 0.5]
        base = 0.0045  # 45 bps/mo headline
        seasonal = 0.002 * math.sin(i / 12.0 * 2 * math.pi)
        shock = 0.0
        # 2009 momentum crash window
        if date(2009, 3, 1) <= d <= date(2009, 6, 30):
            shock = -0.08
        # dot-com bubble burst reversal (2001-Q1)
        if date(2001, 1, 1) <= d <= date(2001, 3, 31):
            shock = -0.04
        ret = base + seasonal + shock + noise * 0.045
        observations.append(
            ReturnObservation(
                period_end=d,
                ret=round(ret, 6),
                rebalance_id=d.strftime("%Y-%m"),
            )
        )

    n = len(observations)
    mean_ret = sum(o.ret for o in observations) / n
    var = sum((o.ret - mean_ret) ** 2 for o in observations) / (n - 1)
    std = math.sqrt(var)
    vol_ann = std * math.sqrt(12)
    ret_ann = mean_ret * 12
    sharpe = ret_ann / vol_ann if vol_ann > 0 else 0.0
    tstat_nw = mean_ret / (std / math.sqrt(n)) if std > 0 else 0.0

    # Simple drawdown on cumulative log-return path
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for o in observations:
        cum += o.ret
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    wins = sum(1 for o in observations if o.ret > 0)
    hit = wins / n

    prov_data = ProvenanceRecord(
        source_id="defeatbeta_yahoo",
        source_tier="primary",
        as_of_date=spec.end_date,
        fidelity_note="Yahoo-sourced universe excludes delisted names — survivorship bias.",
        retrieved_at=datetime(2026, 4, 24, 16, 0, 0, tzinfo=timezone.utc),
    )
    prov_engine = prov_data.child(
        source_id="engine:backtest",
        source_tier="synthesized",
        as_of_date=spec.end_date,
        notes="run_backtest demo fixture — Jegadeesh-Titman 1993 (6,1,6) decile EW, no costs.",
    )

    return BacktestResult(
        spec_hash="demo_jt_1993_6_6",
        start_date=observations[0].period_end,
        end_date=observations[-1].period_end,
        n_periods=n,
        returns=tuple(observations),
        mean_return=round(mean_ret, 6),
        annualized_return=round(ret_ann, 6),
        volatility=round(vol_ann, 6),
        sharpe_ratio=round(sharpe, 4),
        alpha=round(mean_ret, 6),
        alpha_annualized=round(ret_ann, 6),
        alpha_tstat=round(tstat_nw, 3),
        turnover=2.0 / 6,
        max_drawdown=round(max_dd, 4),
        hit_rate=round(hit, 4),
        transaction_cost_bps=0.0,
        return_convention="arithmetic_monthly",
        newey_west_lag=5,
        data_quality_flags=(
            "Yahoo-sourced universe excludes delisted names — survivorship bias.",
            "Exchange tag unavailable in defeatbeta panel — NYSE/AMEX filter was dropped.",
        ),
        warnings=(),
        provenance=prov_engine,
    )


def build_demo_bundle() -> dict[str, Any]:
    spec = _build_spec()
    report = _build_verification_report(spec)
    critique = _build_critique()
    result = _build_backtest(spec)
    verified = VerifiedReplicationSpec(spec=spec, report=report)
    return {
        "paper_id": spec.paper_id,
        "paper_title": spec.paper_title,
        "verified_spec": verified.model_dump(mode="json"),
        "critique": critique.model_dump(mode="json"),
        "backtest": result.model_dump(mode="json"),
        "paper_claim": {
            "monthly_return": 0.0095,
            "tstat": 3.07,
            "window": "Jan 1965 – Dec 1989 (300 months)",
        },
    }


if __name__ == "__main__":
    import json
    from pathlib import Path

    bundle = build_demo_bundle()
    out = Path("outputs/demo_bundle.json")
    out.write_text(json.dumps(bundle, indent=2, default=str))
    print(f"wrote {out}")
