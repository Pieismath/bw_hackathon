"""Tests for D3 — Robustness Adversary."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from src.agents.validation import ROBUSTNESS_ADVERSARY_MODEL
from src.agents.validation.robustness_adversary import judge
from src.specs import (
    BacktestResult,
    PaperClaim,
    ProvenanceRecord,
    ReturnObservation,
    RobustnessJudgment,
    RobustnessScorecard,
    StressTestResult,
    SupportingQuote,
)


def test_d3_uses_opus_47():
    assert ROBUSTNESS_ADVERSARY_MODEL == "claude-opus-4-7"


def _baseline() -> BacktestResult:
    obs = (
        ReturnObservation(period_end=date(2010, 1, 31), ret=0.005),
        ReturnObservation(period_end=date(2010, 2, 28), ret=0.005),
    )
    return BacktestResult(
        spec_hash="h",
        start_date=date(2010, 1, 31),
        end_date=date(2010, 2, 28),
        n_periods=2,
        returns=obs,
        mean_return=0.005,
        annualized_return=0.06,
        volatility=0.05,
        sharpe_ratio=1.2,
        alpha=0.005,
        alpha_annualized=0.06,
        alpha_tstat=2.5,
        turnover=0.3,
        max_drawdown=-0.1,
        hit_rate=0.55,
        provenance=ProvenanceRecord(source_id="t", source_tier="synthesized"),
    )


def _claim() -> PaperClaim:
    return PaperClaim(
        claim_id="c", metric="long_short_monthly_return",
        claimed_value=0.0095, claimed_tstat=3.07,
        claimed_units="fraction_per_month",
        paper_location="Table I",
        supporting_quote=SupportingQuote(text="x", page=1),
    )


def _scorecard() -> RobustnessScorecard:
    tests = (
        StressTestResult(name="lag_0d", family="lag",
                         parameter_swept={"execution_lag_days": 0},
                         headline_metric=0.005, headline_tstat=2.5,
                         n_periods=120, surviving=True),
        StressTestResult(name="cost_25bps", family="costs",
                         parameter_swept={"transaction_cost_bps": 25},
                         headline_metric=-0.001, headline_tstat=-0.5,
                         n_periods=120, surviving=False),
    )
    return RobustnessScorecard(
        baseline_mean_return=0.005,
        baseline_tstat=2.5,
        baseline_n_periods=120,
        tests=tests,
        n_tests=len(tests),
        n_surviving=1,
        families_run=("lag", "costs"),
        fragility_signals=("alpha breaks at 25 bps costs",),
        cost_threshold_bps=15.0,
        lag_half_life_days=8.0,
    )


def test_judge_passes_inputs_through_call_claude():
    """D3 wraps call_claude — verify schema + model are forwarded correctly
    by patching the LLM and asserting the args."""
    expected_judgment = RobustnessJudgment(
        surviving_count=1,
        n_tests=2,
        fragility_signals=("alpha breaks at 25 bps costs",),
        implementable_alpha=0.0035,
        implementable_alpha_basis="cost_10bps result + T+1 lag",
        signal_type="information_based",
        capacity_estimate_usd=None,
        primary_failure_modes=("cost_25bps fails",),
        gap_attribution="methodology_fragility",
        gap_attribution_evidence="cost_25bps and other fragilities dominate",
        confidence="medium",
        summary="ok",
    )
    with patch(
        "src.agents.validation.robustness_adversary.call_claude",
        return_value=expected_judgment,
    ) as mock_llm:
        out = judge(
            scorecard=_scorecard(),
            baseline=_baseline(),
            claim=_claim(),
            diagnosis=None,
            use_cache=False,
        )
    mock_llm.assert_called_once()
    kwargs = mock_llm.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["response_schema"] is RobustnessJudgment
    assert kwargs["use_cache"] is False
    # The user_content should contain the scorecard's marker fields
    assert "cost_25bps" in kwargs["user_content"]
    assert "0.0095" in kwargs["user_content"]  # claim's claimed_value
    assert isinstance(out, RobustnessJudgment)
    assert out.gap_attribution == "methodology_fragility"


def test_judge_returns_judgment_with_diagnosis_in_prompt():
    """When a D2 diagnosis is supplied, it must appear in the LLM input."""
    from src.specs import DivergenceDiagnosis

    diagnosis = DivergenceDiagnosis(
        primary_cause="signal.direction",
        primary_cause_kind="single_field",
        primary_cause_summary="direction flip closed sign",
        primary_cause_evidence="exp #1 closed sign-flip",
        experiments_run=1,
        mutation_results=(),
        alternatives_tested=("signal.direction",),
        alternatives_ruled_out=(),
        residual_abs_gap=0.0124,
        residual_gap_likely_cause="microcap noise + sample window",
        confidence="medium",
        early_exit=True,
    )
    sentinel = RobustnessJudgment(
        surviving_count=1, n_tests=2,
        fragility_signals=(),
        implementable_alpha=0.0,
        implementable_alpha_basis="x",
        signal_type="not_evaluated",
        primary_failure_modes=(),
        gap_attribution="data_limitation",
        gap_attribution_evidence="x",
        confidence="low",
        summary="x",
    )
    with patch(
        "src.agents.validation.robustness_adversary.call_claude",
        return_value=sentinel,
    ) as mock_llm:
        judge(
            scorecard=_scorecard(),
            baseline=_baseline(),
            claim=_claim(),
            diagnosis=diagnosis,
            use_cache=False,
        )
    user_content = mock_llm.call_args.kwargs["user_content"]
    assert "signal.direction" in user_content
    assert "single_field" in user_content


def test_judgment_schema_rejects_too_many_failure_modes():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RobustnessJudgment(
            surviving_count=0, n_tests=0,
            fragility_signals=(),
            implementable_alpha=0.0,
            implementable_alpha_basis="x",
            signal_type="not_evaluated",
            primary_failure_modes=("a", "b", "c", "d", "e", "f"),  # 6 > max 5
            gap_attribution="unexplained",
            gap_attribution_evidence="x",
            confidence="low",
            summary="x",
        )


def test_judgment_schema_rejects_invalid_gap_attribution():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RobustnessJudgment(
            surviving_count=0, n_tests=0,
            fragility_signals=(),
            implementable_alpha=0.0,
            implementable_alpha_basis="x",
            signal_type="not_evaluated",
            primary_failure_modes=(),
            gap_attribution="bogus_value",  # not in literal
            gap_attribution_evidence="x",
            confidence="low",
            summary="x",
        )
