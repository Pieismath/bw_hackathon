"""Tests for D2 — Divergence Diagnostician.

Deterministic unit tests cover:
  - apply_mutation's dotted-path handling and Pydantic coercion.
  - read_current_value.
  - the orchestration loop with a mocked engine + mocked LLM proposer +
    mocked LLM summarizer (verifies: cache hit on duplicate mutation,
    early exit when gap closes, max-experiments cap, fallback narrative).

Live integration is exercised by the consolidated Phase 3 pipeline script.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents.validation import (
    DIVERGENCE_DIAGNOSTICIAN_MODEL,
    apply_mutation,
    diagnose,
    read_current_value,
)
from src.agents.validation import divergence_diagnostician as dd
from src.specs import (
    BacktestResult,
    DivergenceDiagnosis,
    MutationProposal,
    PaperClaim,
    PortfolioSpec,
    ProvenanceRecord,
    RebalanceSpec,
    ReplicationSpec,
    ReturnObservation,
    SignalSpec,
    SupportingQuote,
    UniverseSpec,
)


# ---------------------------------------------------------------------------
# apply_mutation / read_current_value
# ---------------------------------------------------------------------------

def _spec() -> ReplicationSpec:
    return ReplicationSpec(
        paper_id="p",
        paper_title="p",
        universe=UniverseSpec(name="u"),
        signal=SignalSpec(
            name="s", formula="f", inputs=("close",),
            kind="past_return", lookback_months=6, skip_months=0,
            direction="long_high", frequency="monthly",
        ),
        portfolio=PortfolioSpec(
            construction="decile", n_buckets=10, long_bucket=10,
            short_bucket=1, weighting="equal", long_short=True, gross_exposure=2.0,
        ),
        rebalance=RebalanceSpec(
            frequency="monthly", execution_lag_days=1, holding_period_months=6,
        ),
        start_date=date(2000, 1, 1), end_date=date(2020, 12, 31),
    )


def test_apply_mutation_nested_path_coerces_int():
    # Use rebalance.execution_lag_days for the int coercion test — mutating
    # portfolio.long_bucket to 1 would collide with short_bucket=1 and the
    # PortfolioSpec validator would (correctly) reject that single-variable
    # mutation. D2's main loop handles such rejections by logging them.
    spec = _spec()
    out = apply_mutation(spec, "rebalance.execution_lag_days", "5")
    assert out.rebalance.execution_lag_days == 5
    # Other fields preserved
    assert out.portfolio.long_bucket == 10
    assert out.signal.lookback_months == 6


def test_apply_mutation_invalid_combination_raises():
    """Mutating long_bucket to match short_bucket triggers PortfolioSpec's
    validator — the caller (D2 loop) must handle this, not silently accept."""
    spec = _spec()
    import pydantic
    with pytest.raises(pydantic.ValidationError, match="must differ"):
        apply_mutation(spec, "portfolio.long_bucket", "1")


def test_apply_mutation_sign_flip_via_signal_direction():
    """The canonical single-variable sign-flip: change signal.direction.
    This is what D2 should prefer over a two-step long/short swap."""
    spec = _spec()
    out = apply_mutation(spec, "signal.direction", "long_low")
    assert out.signal.direction == "long_low"
    assert out.portfolio.long_bucket == 10  # untouched


def test_apply_mutation_signal_direction_literal():
    spec = _spec()
    out = apply_mutation(spec, "signal.direction", "long_low")
    assert out.signal.direction == "long_low"


def test_apply_mutation_skip_months():
    spec = _spec()
    out = apply_mutation(spec, "signal.skip_months", "1")
    assert out.signal.skip_months == 1
    assert out.signal.lookback_months == 6  # untouched


def test_apply_mutation_rejects_unknown_parameter():
    spec = _spec()
    with pytest.raises(KeyError):
        apply_mutation(spec, "signal.nonexistent_field", "x")


def test_apply_mutation_rejects_depth_3():
    spec = _spec()
    with pytest.raises(ValueError, match="depth"):
        apply_mutation(spec, "a.b.c", "x")


def test_read_current_value_nested():
    spec = _spec()
    assert read_current_value(spec, "portfolio.long_bucket") == "10"
    assert read_current_value(spec, "signal.skip_months") == "0"


# ---------------------------------------------------------------------------
# Configuration sanity
# ---------------------------------------------------------------------------

def test_d2_uses_opus_47():
    assert DIVERGENCE_DIAGNOSTICIAN_MODEL == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Orchestration loop with mocked engine + mocked LLMs
# ---------------------------------------------------------------------------

def _make_result(mean_return: float, tstat: float = 2.0, n: int = 100) -> BacktestResult:
    obs = tuple(
        ReturnObservation(period_end=date(2010, 1 + i % 12, 28), ret=mean_return)
        for i in range(n)
    )
    return BacktestResult(
        spec_hash="h",
        start_date=date(2000, 1, 1),
        end_date=date(2020, 12, 31),
        n_periods=n,
        returns=obs,
        mean_return=mean_return,
        annualized_return=mean_return * 12,
        volatility=0.05,
        sharpe_ratio=mean_return * 12 / 0.05,
        alpha=mean_return,
        alpha_annualized=mean_return * 12,
        alpha_tstat=tstat,
        turnover=0.3,
        max_drawdown=-0.2,
        hit_rate=0.55,
        provenance=ProvenanceRecord(source_id="t", source_tier="synthesized"),
    )


def _claim() -> PaperClaim:
    return PaperClaim(
        claim_id="c",
        metric="long_short_monthly_return",
        claimed_value=0.0095,
        claimed_tstat=3.07,
        claimed_units="fraction_per_month",
        paper_location="Table I",
        supporting_quote=SupportingQuote(text="x", page=1),
    )


def test_diagnose_early_exits_when_first_mutation_closes_gap():
    """Proposer says 'flip long/short'. Engine (mocked) returns a result whose
    mean_return is within 2x tolerance of the claim. D2 stops after 1 experiment
    and emits a diagnosis where the first mutation is the primary cause."""
    spec = _spec()
    baseline = _make_result(-0.01514, tstat=-4.08)  # JT-style sign-flipped gap
    claim = _claim()

    # Mock proposer: single-variable sign flip via signal.direction (the
    # clean way to flip long/short without triggering PortfolioSpec's
    # long != short validator, which a naked long_bucket=1 mutation would).
    proposal = MutationProposal(
        parameter="signal.direction",
        to_value="long_low",
        rationale="A3 flagged decile-ordering ambiguity; direction flip should close sign.",
        expected_direction="close",
    )
    # Mock engine: after the mutation, return +0.0092 (within 0.0003 of claim).
    def fake_propose(**kwargs):
        return proposal
    def fake_engine(mutated_spec, store, transaction_cost_bps=0.0):
        return _make_result(0.0092, tstat=3.0)
    summarized = DivergenceDiagnosis(
        primary_cause="signal.direction",
        primary_cause_kind="single_field",
        primary_cause_summary="Flipping signal.direction closed the sign flip; residual is within tolerance.",
        primary_cause_evidence="Flipping signal.direction closed the sign flip and the residual gap is within tolerance.",
        experiments_run=1,
        mutation_results=(),
        alternatives_tested=("signal.direction",),
        alternatives_ruled_out=(),
        residual_abs_gap=0.0003,
        residual_gap_likely_cause="Sample-window drift (1995-2020 vs paper's 1965-1989) and survivorship bias remain unaccounted for.",
        confidence="high",
        early_exit=True,
        early_exit_reason="gap closed on experiment 1",
    )

    store = MagicMock()
    # Bypass the backtest cache for this unit test
    with patch.object(dd, "_propose_mutation", side_effect=fake_propose), \
         patch.object(dd, "run_backtest_cached", side_effect=fake_engine), \
         patch.object(dd, "_summarize_diagnosis", return_value=summarized):
        diagnosis = diagnose(
            spec=spec,
            baseline_result=baseline,
            claim=claim,
            store=store,
            max_experiments=6,
        )
    assert diagnosis.confidence == "high"
    assert diagnosis.primary_cause == "signal.direction"
    assert diagnosis.early_exit is True


def test_diagnose_runs_to_max_when_no_mutation_closes_gap():
    """Proposer cycles through 6 different mutations. None closes the gap.
    Loop runs to max and reports low confidence."""
    spec = _spec()
    baseline = _make_result(-0.01514, tstat=-4.08)
    claim = _claim()

    # Pool of mutations that all individually validate (don't violate
    # cross-field constraints) but don't close the gap in the fake engine.
    proposals = [
        MutationProposal(parameter="signal.direction", to_value="long_low",
                         rationale="r", expected_direction="close"),
        MutationProposal(parameter="signal.skip_months", to_value="1",
                         rationale="r", expected_direction="close"),
        MutationProposal(parameter="signal.lookback_months", to_value="12",
                         rationale="r", expected_direction="unknown"),
        MutationProposal(parameter="rebalance.holding_period_months", to_value="1",
                         rationale="r", expected_direction="unknown"),
        MutationProposal(parameter="portfolio.weighting", to_value="value",
                         rationale="r", expected_direction="unknown"),
        MutationProposal(parameter="rebalance.execution_lag_days", to_value="5",
                         rationale="r", expected_direction="unknown"),
    ]
    idx = {"i": 0}

    def fake_propose(**kwargs):
        p = proposals[idx["i"] % len(proposals)]
        idx["i"] += 1
        return p

    def fake_engine(mutated_spec, store, transaction_cost_bps=0.0):
        # Every mutation still produces -0.015 — gap never closes
        return _make_result(-0.015, tstat=-4.0)

    summarized = DivergenceDiagnosis(
        primary_cause="no_single_cause_identified",
        primary_cause_kind="other",
        primary_cause_summary="No single-variable mutation closed the gap over 6 experiments.",
        primary_cause_evidence="no single-variable mutation closed the gap",
        experiments_run=6,
        mutation_results=(),
        alternatives_tested=tuple(p.parameter for p in proposals),
        alternatives_ruled_out=tuple(p.parameter for p in proposals),
        residual_abs_gap=abs(-0.01514 - 0.0095),
        residual_gap_likely_cause="multi-variable interactions likely",
        confidence="low",
        early_exit=False,
    )

    store = MagicMock()
    with patch.object(dd, "_propose_mutation", side_effect=fake_propose), \
         patch.object(dd, "run_backtest_cached", side_effect=fake_engine), \
         patch.object(dd, "_summarize_diagnosis", return_value=summarized):
        diagnosis = diagnose(
            spec=spec,
            baseline_result=baseline,
            claim=claim,
            store=store,
            max_experiments=6,
        )
    assert diagnosis.experiments_run == 6
    assert diagnosis.confidence == "low"
    assert diagnosis.early_exit is False


def test_diagnose_stops_on_duplicate_proposal():
    """Proposer repeats itself on iteration 2 — loop should early-exit."""
    spec = _spec()
    baseline = _make_result(-0.01514, tstat=-4.08)
    claim = _claim()

    dup = MutationProposal(parameter="signal.direction", to_value="long_low",
                           rationale="r", expected_direction="close")

    def fake_propose(**kwargs):
        return dup  # always the same

    def fake_engine(mutated_spec, store, transaction_cost_bps=0.0):
        # Mutation doesn't close gap but doesn't error either
        return _make_result(-0.014, tstat=-3.8)

    summarized = DivergenceDiagnosis(
        primary_cause="signal.direction",
        primary_cause_kind="other",
        primary_cause_summary="Only one mutation tested; proposer repeated itself.",
        primary_cause_evidence="only mutation tested",
        experiments_run=1,
        mutation_results=(),
        alternatives_tested=("signal.direction",),
        alternatives_ruled_out=("signal.direction",),
        residual_abs_gap=0.0235,
        residual_gap_likely_cause="proposer repeated itself",
        confidence="low",
        early_exit=True,
        early_exit_reason="proposer repeated mutation",
    )

    store = MagicMock()
    with patch.object(dd, "_propose_mutation", side_effect=fake_propose), \
         patch.object(dd, "run_backtest_cached", side_effect=fake_engine), \
         patch.object(dd, "_summarize_diagnosis", return_value=summarized):
        diagnosis = diagnose(
            spec=spec, baseline_result=baseline, claim=claim, store=store,
            max_experiments=6,
        )
    assert diagnosis.early_exit is True
    assert "repeated" in (diagnosis.early_exit_reason or "")


def test_diagnose_fallback_when_summarizer_fails():
    """If the final LLM summarizer fails, D2 still emits a structured
    diagnosis using the engine-log only."""
    spec = _spec()
    baseline = _make_result(-0.01514, tstat=-4.08)
    claim = _claim()

    proposal = MutationProposal(parameter="signal.direction", to_value="long_low",
                                rationale="r", expected_direction="close")

    def fake_propose(**kwargs):
        return proposal

    def fake_engine(mutated_spec, store, transaction_cost_bps=0.0):
        # mutation closes the gap halfway (still 1%/mo off, so won't early exit)
        return _make_result(-0.005, tstat=-1.5)

    def boom(**kwargs):
        raise RuntimeError("summarizer went pop")

    store = MagicMock()
    with patch.object(dd, "_propose_mutation", side_effect=fake_propose), \
         patch.object(dd, "run_backtest_cached", side_effect=fake_engine), \
         patch.object(dd, "_summarize_diagnosis", side_effect=boom):
        diagnosis = diagnose(
            spec=spec, baseline_result=baseline, claim=claim, store=store,
            max_experiments=1,  # force fallback after 1 exp
        )
    assert isinstance(diagnosis, DivergenceDiagnosis)
    assert diagnosis.confidence == "low"
    assert "fallback" in diagnosis.residual_gap_likely_cause.lower()
