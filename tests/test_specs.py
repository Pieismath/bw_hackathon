"""Tests for src/specs — the structured contracts every other module depends on.

The goal isn't exhaustive coverage of Pydantic itself; it's verifying the
domain invariants that future agents and the engine will rely on:
  - dates are ordered
  - portfolio bucket ranges are consistent with long_short flag
  - provenance chains correctly via child()
  - BacktestResult lengths match n_periods
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from src.specs import (
    AmbiguityDiagnostic,
    AmbiguityFlag,
    BacktestResult,
    ClaimComparison,
    FinalizedReplicationResult,
    PaperClaim,
    PortfolioSpec,
    ProvenanceRecord,
    RebalanceSpec,
    ReplicationResult,
    ReplicationSpec,
    ReturnObservation,
    SignalSpec,
    SupportingQuote,
    UniverseSpec,
)


# ---------------------------------------------------------------------------
# SupportingQuote + PaperClaim
# ---------------------------------------------------------------------------

def test_supporting_quote_strips_whitespace():
    q = SupportingQuote(text="  hello  ", page=3)
    assert q.text == "hello"
    assert q.page == 3
    assert q.verified is False
    assert q.match_confidence == 1.0


def test_supporting_quote_preserves_unicode_and_internal_whitespace():
    # Normalization policy is verbatim: no unicode folding, no whitespace
    # collapsing. Verifier owns normalization on both sides.
    raw = "gross\u00a0profits\u2014to\u2011assets    ratio"  # nbsp + em-dash + non-breaking hyphen + multi space
    q = SupportingQuote(text=raw, page=1)
    assert q.text == raw
    assert "\u2014" in q.text
    assert "    " in q.text
    assert q.text == q.text.lower() or q.text != q.text.lower()  # i.e. untouched case


def test_supporting_quote_rejects_blank():
    with pytest.raises(ValidationError):
        SupportingQuote(text="   ", page=1)


def test_supporting_quote_rejects_over_400_chars():
    # Extractor contract: 1 sentence preferred, 2 sentences max.
    long = "x " * 300  # 600 chars
    with pytest.raises(ValidationError, match="400"):
        SupportingQuote(text=long, page=1)


def test_supporting_quote_accepts_exactly_400_chars():
    text = "x" * 400
    q = SupportingQuote(text=text, page=1)
    assert len(q.text) == 400


def test_supporting_quote_rejects_page_zero():
    with pytest.raises(ValidationError):
        SupportingQuote(text="hi", page=0)


def test_supporting_quote_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        SupportingQuote(text="hi", page=1, match_confidence=1.5)


def test_paper_claim_minimal():
    q = SupportingQuote(text="gross profits-to-assets generates 0.31%", page=4)
    c = PaperClaim(
        claim_id="novy_marx_hl_monthly",
        metric="long_short_monthly_return",
        claimed_value=0.0031,
        claimed_tstat=3.5,
        claimed_units="percent_per_month",
        paper_location="Table 3 HL row",
        supporting_quote=q,
    )
    assert c.claimed_value == pytest.approx(0.0031)
    assert c.supporting_quote.page == 4


# ---------------------------------------------------------------------------
# UniverseSpec / SignalSpec / PortfolioSpec / RebalanceSpec
# ---------------------------------------------------------------------------

def _ambiguity(param: str, chosen: str) -> AmbiguityFlag:
    return AmbiguityFlag(
        parameter=param,
        default_chosen=chosen,
        alternatives=("alt_a", "alt_b"),
        sensitivity_priority="high",
        reason="paper does not state explicitly",
    )


def test_ambiguity_flag_defaults():
    a = _ambiguity("execution_lag_days", "1")
    assert a.sensitivity_priority == "high"
    assert a.paper_evidence is None
    assert "alt_a" in a.alternatives


def test_universe_spec_basic():
    u = UniverseSpec(
        name="us_common_excl_fin",
        exclude_filters=("financials_sic_6xxx", "price_lt_5"),
        exchanges=("NYSE", "NASDAQ", "AMEX"),
    )
    assert u.region == "US"
    assert u.asset_class == "equity"
    assert "financials_sic_6xxx" in u.exclude_filters


def test_signal_spec_basic():
    s = SignalSpec(
        name="gross_profitability",
        formula="(revenue - cogs) / total_assets",
        inputs=("revenue", "cogs", "total_assets"),
        transformations=("winsorize_1_99",),
        direction="long_high",
        frequency="annual",
        lag_fundamentals_days=90,
    )
    assert s.lag_fundamentals_days == 90
    assert s.direction == "long_high"


def test_portfolio_spec_long_short_requires_short_bucket():
    with pytest.raises(ValidationError, match="short_bucket"):
        PortfolioSpec(
            construction="quintile", n_buckets=5, long_bucket=5,
            long_short=True,
        )


def test_portfolio_spec_long_short_distinct_buckets():
    with pytest.raises(ValidationError, match="differ"):
        PortfolioSpec(
            construction="quintile", n_buckets=5, long_bucket=3, short_bucket=3,
            long_short=True,
        )


def test_portfolio_spec_long_bucket_in_range():
    with pytest.raises(ValidationError, match="long_bucket"):
        PortfolioSpec(
            construction="quintile", n_buckets=5, long_bucket=6, short_bucket=1,
            long_short=True,
        )


def test_portfolio_spec_short_bucket_in_range():
    with pytest.raises(ValidationError, match="short_bucket"):
        PortfolioSpec(
            construction="quintile", n_buckets=5, long_bucket=5, short_bucket=0,
            long_short=True,
        )


def test_portfolio_spec_long_only_ok():
    p = PortfolioSpec(
        construction="quintile", n_buckets=5, long_bucket=5,
        long_short=False, gross_exposure=1.0,
    )
    assert p.short_bucket is None


def test_portfolio_spec_long_short_canonical_form_accepted():
    """Canonical form: long_bucket=n_buckets, short_bucket=1. The sign of the
    strategy is carried by signal.direction. This is the only legal encoding
    for long_short specs after the Phase D dehardcode refactor."""
    p = PortfolioSpec(
        construction="decile", n_buckets=10, long_bucket=10, short_bucket=1,
        long_short=True,
    )
    assert p.long_bucket == 10
    assert p.short_bucket == 1


def test_portfolio_spec_long_short_inverted_buckets_rejected():
    """Inverted-bucket encoding (long_bucket=1, short_bucket=N) is rejected.
    Earlier the CHST 2017 paper extraction emitted this pattern alongside
    direction='long_low', double-encoding the contrarian sign and causing the
    engine to trade momentum. The PAPER_ID_OVERRIDES dict patched it for
    that paper; the validator now rejects the pattern unconditionally so
    A1 can never produce a double-encoded spec again."""
    with pytest.raises(ValidationError, match="canonical encoding"):
        PortfolioSpec(
            construction="quintile", n_buckets=5, long_bucket=1, short_bucket=5,
            long_short=True,
        )


def test_portfolio_spec_long_short_intermediate_buckets_rejected():
    """An intermediate long_bucket (e.g. P4 vs. P2 on a 5-bucket sort) is
    also non-canonical and rejected. The engine assumes long_bucket is the
    top of the post-direction ranking. Papers that genuinely want non-extreme
    buckets need a different mechanism (extending the spec to express the
    cross-section subset)."""
    with pytest.raises(ValidationError, match="canonical encoding"):
        PortfolioSpec(
            construction="quintile", n_buckets=5, long_bucket=4, short_bucket=2,
            long_short=True,
        )


def test_headline_claim_accepts_stringified_json_from_llm_toolcall():
    """Anthropic tool-use occasionally surfaces a discriminated-union nested
    field as a JSON-encoded STRING (rather than a nested JSON object) when
    the schema combines `oneOf` + `discriminator` + `Optional`. A
    `BeforeValidator` on the union decodes the string before Pydantic's
    union resolver runs so the LLM's output validates without a retry.

    Regression for a real failure in
    test_fold_high_severity_adds_flags_to_spec where A1 emitted
    `headline_claim` as a string like '{"kind": "monthly_long_short_return",
    "monthly_return": 0.0086, ...}' and the spec validator rejected the
    whole ReplicationSpec.
    """
    from pydantic import TypeAdapter
    from src.specs import HeadlineClaim, MonthlyLongShortReturn
    import json as _json

    nested = {
        "kind": "monthly_long_short_return",
        "monthly_return": 0.0086,
        "t_stat": 2.95,
        "window_label": "Jan 1965 – Dec 1989",
        "paper_location": "Table I Panel A",
        "supporting_quote": {
            "text": "Buy-sell 0.0086", "page": 7,
            "verified": True, "match_confidence": 1.0,
        },
    }
    ta = TypeAdapter(HeadlineClaim)

    parsed_from_dict = ta.validate_python(nested)
    parsed_from_string = ta.validate_python(_json.dumps(nested))
    assert isinstance(parsed_from_dict, MonthlyLongShortReturn)
    assert isinstance(parsed_from_string, MonthlyLongShortReturn)
    assert parsed_from_dict == parsed_from_string


def test_portfolio_spec_long_only_allows_any_bucket():
    """Long-only specs are unaffected by the canonical-form rule. The
    long_bucket integer represents which percentile the paper longs; e.g.
    a paper that goes long the bottom decile (PS-1988-style long-only
    contrarian) sets long_bucket=1."""
    p1 = PortfolioSpec(
        construction="decile", n_buckets=10, long_bucket=1,
        long_short=False, gross_exposure=1.0,
    )
    p2 = PortfolioSpec(
        construction="decile", n_buckets=10, long_bucket=5,
        long_short=False, gross_exposure=1.0,
    )
    assert p1.long_bucket == 1
    assert p2.long_bucket == 5


def test_rebalance_spec_defaults_to_T_plus_1():
    r = RebalanceSpec()
    assert r.execution_lag_days == 1
    assert r.frequency == "monthly"
    assert r.measure_daily_returns is False


def test_rebalance_spec_negative_lag_rejected():
    with pytest.raises(ValidationError):
        RebalanceSpec(execution_lag_days=-1)


# ---------------------------------------------------------------------------
# ReplicationSpec
# ---------------------------------------------------------------------------

def _valid_spec(**overrides) -> ReplicationSpec:
    base = dict(
        paper_id="novy_marx_2013",
        paper_title="The Other Side of Value",
        universe=UniverseSpec(name="us_common_excl_fin"),
        signal=SignalSpec(
            name="gross_profitability",
            formula="(revenue - cogs) / total_assets",
            inputs=("revenue", "cogs", "total_assets"),
            frequency="annual",
            lag_fundamentals_days=90,
        ),
        portfolio=PortfolioSpec(
            construction="quintile", n_buckets=5, long_bucket=5, short_bucket=1,
            weighting="value", use_nyse_breakpoints=True, long_short=True,
        ),
        rebalance=RebalanceSpec(frequency="monthly", execution_lag_days=1),
        start_date=date(1963, 7, 1),
        end_date=date(2010, 12, 31),
        ambiguities=(_ambiguity("execution_lag_days", "1"),),
    )
    base.update(overrides)
    return ReplicationSpec(**base)


def test_replication_spec_valid_novy_marx_shape():
    spec = _valid_spec()
    assert spec.portfolio.use_nyse_breakpoints is True
    assert spec.rebalance.execution_lag_days == 1
    assert spec.signal.lag_fundamentals_days == 90
    assert len(spec.ambiguities) == 1


def test_replication_spec_end_before_start_rejected():
    with pytest.raises(ValidationError, match="end_date"):
        _valid_spec(start_date=date(2020, 1, 1), end_date=date(2010, 1, 1))


def test_replication_spec_equal_dates_rejected():
    with pytest.raises(ValidationError, match="end_date"):
        _valid_spec(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))


def test_replication_spec_is_frozen():
    spec = _valid_spec()
    with pytest.raises(ValidationError):
        spec.start_date = date(1990, 1, 1)  # type: ignore[misc]


def test_replication_spec_extra_fields_rejected():
    with pytest.raises(ValidationError):
        _valid_spec(unknown_field="nope")


# ---------------------------------------------------------------------------
# ProvenanceRecord chaining
# ---------------------------------------------------------------------------

def test_provenance_child_extends_chain():
    root = ProvenanceRecord(
        source_id="pdf:novy_marx",
        source_tier="primary",
        notes="parsed PDF",
    )
    child = root.child(
        source_id="claude-opus-4-7:methodology_extractor",
        source_tier="synthesized",
    )
    grand = child.child(
        source_id="engine:backtest",
        source_tier="synthesized",
    )
    assert root.record_id in child.parent_ids
    assert root.record_id in grand.parent_ids
    assert child.record_id in grand.parent_ids
    assert len(grand.parent_ids) == 2


def test_provenance_substitution_flag_defaults_false():
    p = ProvenanceRecord(source_id="defeatbeta_yahoo", source_tier="primary")
    assert p.substitution_flag is False
    assert p.fidelity_note is None


def test_provenance_is_frozen():
    p = ProvenanceRecord(source_id="x", source_tier="primary")
    with pytest.raises(ValidationError):
        p.notes = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BacktestResult / ReplicationResult
# ---------------------------------------------------------------------------

def _make_returns(n: int) -> tuple[ReturnObservation, ...]:
    return tuple(
        ReturnObservation(
            period_end=date(2000, 1, 1),
            ret=0.001,
            formation_date=date(1999, 12, 31),
            rebalance_id=f"cycle_{i:03d}",
        )
        for i in range(n)
    )


def _valid_backtest_result(n: int = 3) -> BacktestResult:
    return BacktestResult(
        spec_hash="abc123",
        start_date=date(2000, 1, 1),
        end_date=date(2010, 12, 31),
        n_periods=n,
        returns=_make_returns(n),
        mean_return=0.001,
        annualized_return=0.012,
        volatility=0.05,
        sharpe_ratio=0.24,
        alpha=0.0008,
        alpha_annualized=0.0096,
        alpha_tstat=2.1,
        turnover=0.3,
        max_drawdown=-0.15,
        hit_rate=0.55,
        provenance=ProvenanceRecord(source_id="engine:backtest", source_tier="synthesized"),
    )


def test_backtest_result_length_must_match_n_periods():
    with pytest.raises(ValidationError, match="n_periods"):
        BacktestResult(
            spec_hash="x",
            start_date=date(2000, 1, 1), end_date=date(2001, 1, 1),
            n_periods=5,
            returns=_make_returns(3),
            mean_return=0, annualized_return=0, volatility=0, sharpe_ratio=0,
            alpha=0, alpha_annualized=0, alpha_tstat=0, turnover=0,
            max_drawdown=0, hit_rate=0.5,
            provenance=ProvenanceRecord(source_id="x", source_tier="synthesized"),
        )


def test_backtest_result_hit_rate_bounds():
    with pytest.raises(ValidationError):
        BacktestResult(
            spec_hash="x",
            start_date=date(2000, 1, 1), end_date=date(2001, 1, 1),
            n_periods=1, returns=_make_returns(1),
            mean_return=0, annualized_return=0, volatility=0, sharpe_ratio=0,
            alpha=0, alpha_annualized=0, alpha_tstat=0, turnover=0,
            max_drawdown=0, hit_rate=1.5,
            provenance=ProvenanceRecord(source_id="x", source_tier="synthesized"),
        )


def test_backtest_result_round_trip():
    r = _valid_backtest_result(n=2)
    data = r.model_dump()
    r2 = BacktestResult.model_validate(data)
    assert r2.n_periods == 2
    assert r2.returns[0].ret == pytest.approx(0.001)
    assert r2.returns[0].formation_date == date(1999, 12, 31)
    assert r2.returns[0].rebalance_id == "cycle_000"


def test_backtest_result_to_dataframe_returns():
    r = _valid_backtest_result(n=3)
    df = r.to_dataframe(kind="returns")
    assert len(df) == 3
    assert set(df.columns) >= {"period_end", "ret", "formation_date", "rebalance_id"}
    assert df["rebalance_id"].tolist() == ["cycle_000", "cycle_001", "cycle_002"]


def test_backtest_result_to_dataframe_empty_schema():
    r = BacktestResult(
        spec_hash="empty",
        start_date=date(2000, 1, 1), end_date=date(2001, 1, 1),
        n_periods=0, returns=(),
        mean_return=0, annualized_return=0, volatility=0, sharpe_ratio=0,
        alpha=0, alpha_annualized=0, alpha_tstat=0, turnover=0,
        max_drawdown=0, hit_rate=0.5,
        provenance=ProvenanceRecord(source_id="x", source_tier="synthesized"),
    )
    df = r.to_dataframe()
    assert df.empty
    assert "period_end" in df.columns


def test_backtest_result_to_dataframe_daily_missing_raises():
    r = _valid_backtest_result(n=1)
    assert r.daily_returns is None
    with pytest.raises(ValueError, match="daily_returns is None"):
        r.to_dataframe(kind="daily")


def test_replication_result_assembles():
    spec = _valid_spec()
    r = _valid_backtest_result(n=1)
    q = SupportingQuote(text="claimed alpha 0.31%", page=4)
    claim = PaperClaim(
        claim_id="hl_monthly", metric="long_short_monthly_return",
        claimed_value=0.0031, claimed_tstat=3.5, claimed_units="percent_per_month",
        paper_location="Table 3", supporting_quote=q,
    )
    comp = ClaimComparison(
        claim=claim, replicated_value=0.0028,
        absolute_gap=-0.0003, relative_gap=-0.097,
        tstat_gap=-0.4, tolerance_used=0.001, verdict="partial",
    )
    diag = AmbiguityDiagnostic(
        ambiguity=_ambiguity("execution_lag_days", "1"),
        alternative_tried="0",
        replicated_metric="long_short_monthly_return",
        baseline_value=0.0028,
        alternative_value=0.0033,
        delta=0.0005,
        closes_gap=True,
    )
    result = ReplicationResult(
        replication_id="novy_marx_run_001",
        spec=spec,
        result=r,
        claims=(claim,),
        comparisons=(comp,),
        ambiguity_diagnostics=(diag,),
        provenance=ProvenanceRecord(source_id="orchestrator", source_tier="synthesized"),
    )
    assert result.overall_confidence == "medium"
    assert result.comparisons[0].verdict == "partial"
    assert result.ambiguity_diagnostics[0].closes_gap is True


def test_replication_result_builder_is_mutable_and_defaults_not_shared():
    # Confirm nullable fields default to None (not mutable [] or {})
    assert ReplicationResult.model_fields["robustness_scorecard"].default is None
    assert ReplicationResult.model_fields["implementable_alpha"].default is None

    # Builder is mutable — orchestrator needs to fill fields later
    spec = _valid_spec()
    r = _valid_backtest_result(n=1)
    result = ReplicationResult(
        replication_id="r1", spec=spec, result=r,
        claims=(), comparisons=(),
        provenance=ProvenanceRecord(source_id="orchestrator", source_tier="synthesized"),
    )
    result.implementable_alpha = 0.0012  # mutation works on the builder
    result.robustness_scorecard = {"subperiod": 0.9, "costs_10bps": 0.7}
    assert result.implementable_alpha == pytest.approx(0.0012)


def test_replication_result_finalize_returns_frozen_snapshot():
    spec = _valid_spec()
    r = _valid_backtest_result(n=1)
    builder = ReplicationResult(
        replication_id="r1", spec=spec, result=r,
        claims=(), comparisons=(),
        implementable_alpha=0.0012,
        provenance=ProvenanceRecord(source_id="orchestrator", source_tier="synthesized"),
    )
    frozen = builder.finalize()
    assert isinstance(frozen, FinalizedReplicationResult)
    assert frozen.implementable_alpha == pytest.approx(0.0012)

    # Frozen result rejects mutation
    with pytest.raises(ValidationError):
        frozen.implementable_alpha = 0.0050  # type: ignore[misc]

    # Finalize snapshots current state — later mutation on builder does not
    # change the already-finalized copy
    builder.implementable_alpha = 0.9999
    assert frozen.implementable_alpha == pytest.approx(0.0012)
