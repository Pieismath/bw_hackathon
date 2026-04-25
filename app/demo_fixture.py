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
    DivergenceDiagnosis,
    FormationDecayPoint,
    HeadlineClaim,
    MutationProposal,
    MutationResult,
    PortfolioSpec,
    ProvenanceRecord,
    QuoteVerification,
    RebalanceSpec,
    ReplicationSpec,
    ReturnObservation,
    RobustnessJudgment,
    RobustnessScorecard,
    SignalSpec,
    StressTestResult,
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
        headline_claim=HeadlineClaim(
            metric="monthly_long_short_return",
            monthly_return=0.0095,
            t_stat=3.07,
            window_label="Jan 1965 – Dec 1989 (300 months)",
            paper_location="Table I Panel A, J=6/K=6 'Buy-sell' row",
            supporting_quote=SupportingQuote(
                text="Buy-sell 0.0095",
                page=7,
                verified=True,
                match_confidence=1.0,
            ),
        ),
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
        # Illustrative post-formation decay profile — JT-style momentum peaks
        # mid-holding and fades; these magnitudes are consistent with a demo
        # ~0.43%/mo aggregate strategy averaged over 6 active tranches.
        decay_by_age=(
            FormationDecayPoint(age_months=1, mean_ret=0.0061, std_error=0.0009, n_observations=312),
            FormationDecayPoint(age_months=2, mean_ret=0.0075, std_error=0.0009, n_observations=312),
            FormationDecayPoint(age_months=3, mean_ret=0.0068, std_error=0.0010, n_observations=312),
            FormationDecayPoint(age_months=4, mean_ret=0.0051, std_error=0.0010, n_observations=312),
            FormationDecayPoint(age_months=5, mean_ret=0.0028, std_error=0.0011, n_observations=312),
            FormationDecayPoint(age_months=6, mean_ret=0.0002, std_error=0.0012, n_observations=312),
        ),
        data_quality_flags=(
            "Yahoo-sourced universe excludes delisted names — survivorship bias.",
            "Exchange tag unavailable in defeatbeta panel — NYSE/AMEX filter was dropped.",
        ),
        warnings=(),
        provenance=prov_engine,
    )


def _build_robustness() -> dict[str, Any]:
    """Demo robustness scorecard + D3 judgment, consistent with the JT-1993 backtest."""

    tests: list[StressTestResult] = [
        # --- lag family (4 rows) ---
        StressTestResult(
            name="lag_1d",
            family="lag",
            parameter_swept={"signal_lag_days": 1},
            headline_metric=0.0045,
            headline_tstat=2.80,
            n_periods=312,
            surviving=True,
            notes="Baseline T+1 execution.",
        ),
        StressTestResult(
            name="lag_2d",
            family="lag",
            parameter_swept={"signal_lag_days": 2},
            headline_metric=0.0036,
            headline_tstat=2.21,
            n_periods=312,
            surviving=True,
            notes="Realistic execution: month-end close + next-day open + slippage buffer.",
        ),
        StressTestResult(
            name="lag_5d",
            family="lag",
            parameter_swept={"signal_lag_days": 5},
            headline_metric=0.0010,
            headline_tstat=0.62,
            n_periods=312,
            surviving=False,
            notes="Alpha decays to ~10 bps/mo by 5-day lag.",
        ),
        StressTestResult(
            name="lag_10d",
            family="lag",
            parameter_swept={"signal_lag_days": 10},
            headline_metric=-0.0008,
            headline_tstat=-0.49,
            n_periods=312,
            surviving=False,
            notes="Sign flips by 10 business days — short-horizon information signal.",
        ),
        # --- costs family (3 rows) ---
        StressTestResult(
            name="costs_0bps",
            family="costs",
            parameter_swept={"transaction_cost_bps": 0.0},
            headline_metric=0.0045,
            headline_tstat=2.80,
            n_periods=312,
            surviving=True,
            notes="Gross-of-cost baseline.",
        ),
        StressTestResult(
            name="costs_10bps",
            family="costs",
            parameter_swept={"transaction_cost_bps": 10.0},
            headline_metric=0.0028,
            headline_tstat=1.76,
            n_periods=312,
            surviving=True,
            notes="10 bps round-trip per rebalance — alpha clipped but positive.",
        ),
        StressTestResult(
            name="costs_25bps",
            family="costs",
            parameter_swept={"transaction_cost_bps": 25.0},
            headline_metric=-0.0003,
            headline_tstat=-0.18,
            n_periods=312,
            surviving=False,
            notes="25 bps per rebalance erases the spread — alpha crosses zero around 18 bps.",
        ),
        # --- subperiod family (3 rows) ---
        StressTestResult(
            name="subperiod_pre_2008",
            family="subperiod",
            parameter_swept={"start_date": "1995-01-31", "end_date": "2007-12-31"},
            headline_metric=0.0061,
            headline_tstat=2.94,
            n_periods=156,
            surviving=True,
            notes="Strong pre-crisis performance.",
        ),
        StressTestResult(
            name="subperiod_crisis_2008_09",
            family="subperiod",
            parameter_swept={"start_date": "2008-01-31", "end_date": "2009-12-31"},
            headline_metric=-0.0182,
            headline_tstat=-1.41,
            n_periods=24,
            surviving=False,
            notes="2009 momentum crash drives a sharp negative subperiod.",
        ),
        StressTestResult(
            name="subperiod_post_2010",
            family="subperiod",
            parameter_swept={"start_date": "2010-01-31", "end_date": "2020-12-31"},
            headline_metric=0.0021,
            headline_tstat=1.18,
            n_periods=132,
            surviving=False,
            notes="Post-publication regime: alpha attenuates and is no longer significant.",
        ),
        # --- liquidity family (2 rows) ---
        StressTestResult(
            name="liquidity_min_price_5",
            family="liquidity",
            parameter_swept={"min_price": 5.0},
            headline_metric=0.0045,
            headline_tstat=2.80,
            n_periods=312,
            surviving=True,
            notes="Baseline universe filter.",
        ),
        StressTestResult(
            name="liquidity_min_price_20",
            family="liquidity",
            parameter_swept={"min_price": 20.0},
            headline_metric=0.0022,
            headline_tstat=1.34,
            n_periods=312,
            surviving=False,
            notes="Dropping low-price names cuts the spread roughly in half — alpha is concentrated in cheaper, less-liquid names.",
        ),
        # --- data_quality family (1 row) ---
        StressTestResult(
            name="data_quality_survivorship",
            family="data_quality",
            parameter_swept={"flag": "survivorship_bias"},
            headline_metric=0.0045,
            headline_tstat=2.80,
            n_periods=312,
            surviving=True,
            notes="Yahoo-sourced universe excludes delisted names — survivorship bias likely inflates the headline by 10-20 bps/mo.",
        ),
        # --- capacity family (1 row) ---
        StressTestResult(
            name="capacity_50bps_impact",
            family="capacity",
            parameter_swept={"impact_bps": 50.0},
            headline_metric=500_000_000.0,
            headline_tstat=None,
            n_periods=312,
            surviving=True,
            notes="Estimated AUM at which trading impact reaches 50 bps round-trip is ~$500M.",
        ),
    ]

    n_surviving = sum(1 for t in tests if t.surviving)
    families_run = ("lag", "costs", "subperiod", "liquidity", "data_quality", "capacity")
    fragility_signals = (
        "alpha sign-flips in 1 subperiod: crisis_2008_09",
        "liquidity-sensitive: mean return falls from +0.45%/mo at min_price=5 to +0.22%/mo at min_price=20",
        "alpha decays fast: half-life ≈ 4.5 business days",
    )

    scorecard = RobustnessScorecard(
        baseline_mean_return=0.0045,
        baseline_tstat=2.80,
        baseline_n_periods=312,
        tests=tuple(tests),
        n_tests=len(tests),
        n_surviving=n_surviving,
        families_run=families_run,
        fragility_signals=fragility_signals,
        cost_threshold_bps=18.0,
        lag_half_life_days=4.5,
        capacity_estimate_usd=500_000_000.0,
    )

    judgment = RobustnessJudgment(
        surviving_count=n_surviving,
        n_tests=len(tests),
        fragility_signals=fragility_signals,
        implementable_alpha=0.0014,
        implementable_alpha_basis=(
            "Realistic T+2 execution (lag_2d row at 36 bps/mo) net of 25 bps round-trip costs "
            "leaves roughly 14 bps/mo of harvestable alpha; the 5-day-lag and 25-bps-cost rows "
            "both fall to or below zero, so anything beyond a 2-day execution window is unviable."
        ),
        signal_type="information_based",
        capacity_estimate_usd=500_000_000.0,
        primary_failure_modes=("execution_lag_decay", "subperiod_instability"),
        gap_attribution="post_publication_decay",
        gap_attribution_evidence=(
            "JT report 0.95%/mo on 1965–1989; the post-2010 subperiod row delivers only 0.21%/mo "
            "at t=1.18, while the pre-2008 row remains strong at 0.61%/mo. The headline gap is "
            "therefore consistent with regime decay after the strategy was published, not with "
            "implementation error in the replication itself."
        ),
        confidence="medium",
        summary=(
            "The replicated long-short momentum spread survives in 9 of 14 stress tests, with "
            "alpha concentrated in low-lag execution and the pre-2010 sample. Two structural "
            "fragilities dominate: alpha decays with a ~4.5-business-day half-life (so any "
            "execution slower than T+2 forfeits most of the spread) and the 2008-09 momentum "
            "crash flips the subperiod sign. After realistic 25-bps round-trip costs the "
            "implementable alpha is about 14 bps/mo, with capacity around $500M AUM. The gap "
            "between the paper and our replication is most plausibly explained by post-"
            "publication decay in the post-2010 regime, not by a methodological error."
        ),
    )

    return {
        "scorecard": scorecard.model_dump(mode="json"),
        "judgment": judgment.model_dump(mode="json"),
    }


def _build_diagnosis() -> dict[str, Any]:
    """Demo D2 divergence diagnosis: paper claims 0.95%/mo, replication 0.45%/mo."""

    # Mutation 1 — restrict end_date to 2000-12-31 (closes most of the gap).
    mut1 = MutationResult(
        proposal=MutationProposal(
            parameter="end_date",
            to_value="2000-12-31",
            rationale=(
                "JT's headline window is 1965–1989. Our 1995–2020 sample mixes the post-"
                "publication decay regime with the pre-decay one. Restricting to pre-2000 "
                "isolates the era closest to the paper's own."
            ),
            expected_direction="close",
        ),
        from_value_human="2020-12-31",
        pre_abs_gap=0.0050,
        post_abs_gap=0.0012,
        gap_delta=0.0038,
        pre_mean_return=0.0045,
        post_mean_return=0.0083,
        pre_tstat=2.80,
        post_tstat=2.61,
        verdict_before="diverged",
        verdict_after="partial",
        closed_sign_flip=False,
        notes=(
            "Restricting to 1995-2000 raises mean monthly return from 0.45% to 0.83%, "
            "closing 76% of the original gap to the 0.95% claim."
        ),
    )

    # Mutation 2 — add a 1-month skip (gap widens).
    mut2 = MutationResult(
        proposal=MutationProposal(
            parameter="signal.skip_months",
            to_value="1",
            rationale=(
                "JT-adjacent literature inserts a 1-month skip between formation and holding "
                "to avoid bid-ask bounce; testing whether that explains the gap."
            ),
            expected_direction="unknown",
        ),
        from_value_human="0",
        pre_abs_gap=0.0050,
        post_abs_gap=0.0058,
        gap_delta=-0.0008,
        pre_mean_return=0.0045,
        post_mean_return=0.0037,
        pre_tstat=2.80,
        post_tstat=2.31,
        verdict_before="diverged",
        verdict_after="diverged",
        closed_sign_flip=False,
        notes="Adding a 1-month skip slightly hurts the spread; not the primary cause.",
    )

    # Mutation 3 — switch to value weighting (gap widens).
    mut3 = MutationResult(
        proposal=MutationProposal(
            parameter="portfolio.weighting",
            to_value="value",
            rationale=(
                "Equal weighting overweights small caps where momentum is strongest. "
                "Switching to value weighting tests whether the small-cap tilt is what "
                "is keeping our replication low relative to JT's headline."
            ),
            expected_direction="unknown",
        ),
        from_value_human="equal",
        pre_abs_gap=0.0050,
        post_abs_gap=0.0059,
        gap_delta=-0.0009,
        pre_mean_return=0.0045,
        post_mean_return=0.0036,
        pre_tstat=2.80,
        post_tstat=2.18,
        verdict_before="diverged",
        verdict_after="diverged",
        closed_sign_flip=False,
        notes=(
            "Value weighting tightens the spread further; the small-cap tilt was actually "
            "helping, not hurting, the headline."
        ),
    )

    diagnosis = DivergenceDiagnosis(
        primary_cause="sample_window_post_2000",
        primary_cause_kind="data_window",
        primary_cause_summary=(
            "Restricting to pre-2000 recovers 0.83%/mo, within 12bps of the 0.95% claim."
        ),
        primary_cause_evidence=(
            "Mutation end_date=2000-12-31 closes 76% of the 50bps gap (post_abs_gap=0.0012, "
            "post_mean_return=0.0083), while skip_months and value-weighting both widen the "
            "gap. The single mutation that restricts the sample to JT's own era is the only "
            "one that meaningfully closes the divergence."
        ),
        experiments_run=3,
        mutation_results=(mut1, mut2, mut3),
        alternatives_tested=("end_date", "signal.skip_months", "portfolio.weighting"),
        alternatives_ruled_out=("signal.skip_months", "portfolio.weighting"),
        residual_abs_gap=0.0012,
        residual_gap_likely_cause=(
            "The 12bps residual is most plausibly attributable to universe coverage "
            "differences: the defeatbeta panel excludes delisted names (survivorship bias) "
            "and lacks NYSE/AMEX exchange tags, both of which JT relied on directly."
        ),
        confidence="high",
        early_exit=False,
        early_exit_reason=None,
    )

    return {
        "diagnosis": diagnosis.model_dump(mode="json"),
        "n_experiments": diagnosis.experiments_run,
    }


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
        # Bundle-root paper_claim is the legacy flat shape consumed by the
        # frontend's runDiagnosis. Derive from spec.headline_claim so the
        # source of truth stays on the spec.
        "paper_claim": _bundle_paper_claim(spec.headline_claim),
        "robustness": _build_robustness(),
        "diagnosis": _build_diagnosis(),
    }


def _bundle_paper_claim(hc: HeadlineClaim | None) -> dict[str, Any] | None:
    """Project a HeadlineClaim onto the legacy flat bundle shape D2 frontend reads."""
    if hc is None:
        return None
    return {
        "monthly_return": hc.monthly_return,
        "tstat": hc.t_stat,
        "window": hc.window_label,
    }


if __name__ == "__main__":
    import json
    from pathlib import Path

    bundle = build_demo_bundle()
    out = Path("outputs/demo_bundle.json")
    out.write_text(json.dumps(bundle, indent=2, default=str))
    print(f"wrote {out}")
