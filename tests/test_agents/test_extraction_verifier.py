"""Tests for A2 — Extraction Verifier.

Deterministic tests construct synthetic specs with known-good and known-bad
quotes and assert the retry/fail classification. LLM-marked tests run the
full Haiku path and the happy-path end-to-end on the JT PDF.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.agents.extraction import (
    EXTRACTION_VERIFIER_MODEL,
    build_retry_feedback,
    should_retry,
    verify_spec,
)
from src.pdf.parser import parse_pdf
from src.specs import (
    AmbiguityFlag,
    PortfolioSpec,
    RebalanceSpec,
    ReplicationSpec,
    SignalSpec,
    SupportingQuote,
    UniverseSpec,
)

JT_PDF = Path("data/papers/jegadeesh_titman_1993_momentum.pdf")

# Phrase that is demonstrably page-7 unique on the JT PDF (used elsewhere in
# the suite). Keep this near the imports so regenerations are localised.
JT_P7_VERBATIM = "The values of J and K for the different strategies"


def _base_spec(**overrides) -> ReplicationSpec:
    """A minimally-valid JT-shaped ReplicationSpec factory."""
    fields = dict(
        paper_id="jt_test",
        paper_title="Returns to Buying Winners and Selling Losers",
        universe=UniverseSpec(name="us_common"),
        signal=SignalSpec(
            name="past_return",
            formula="f",
            inputs=("close",),
            kind="past_return",
            lookback_months=6,
            skip_months=0,
            frequency="monthly",
        ),
        portfolio=PortfolioSpec(
            construction="decile",
            n_buckets=10,
            long_bucket=10,
            short_bucket=1,
            weighting="equal",
            long_short=True,
            gross_exposure=2.0,
        ),
        rebalance=RebalanceSpec(
            frequency="monthly",
            execution_lag_days=0,
            holding_period_months=6,
        ),
        start_date=date(1965, 1, 1),
        end_date=date(1989, 12, 31),
    )
    fields.update(overrides)
    return ReplicationSpec(**fields)


@pytest.fixture(scope="module")
def jt_pdf():
    return parse_pdf(JT_PDF)


# ---------------------------------------------------------------------------
# Deterministic path (use_llm_check=False) — classification logic
# ---------------------------------------------------------------------------

def test_verifier_model_is_haiku():
    # A2 must be cheap; regression catch against accidental Opus routing.
    assert EXTRACTION_VERIFIER_MODEL == "claude-haiku-4-5-20251001"


def test_verify_passes_when_quote_exact_at_claimed_page(jt_pdf):
    q = SupportingQuote(text=JT_P7_VERBATIM, page=7)
    spec = _base_spec(
        signal=SignalSpec(
            name="past_return",
            formula="f",
            inputs=("close",),
            kind="past_return",
            lookback_months=6,
            skip_months=0,
            frequency="monthly",
            supporting_quote=q,
        ),
    )
    report = verify_spec(spec, jt_pdf, use_llm_check=False)
    assert report.n_failed_high == 0
    assert report.n_checks == 1
    check = report.checks[0]
    assert check.verification_status == "verified"
    assert check.verified_page == 7
    assert check.failed is False


def test_verify_fails_on_fabricated_high_severity_quote(jt_pdf):
    # Fabricated quote not anywhere in the PDF → not_found, triggers retry
    q = SupportingQuote(
        text="This sentence has never been published in any paper ever.",
        page=7,
    )
    spec = _base_spec(
        signal=SignalSpec(
            name="past_return", formula="f", inputs=("close",),
            kind="past_return", lookback_months=6, skip_months=0,
            frequency="monthly",
            supporting_quote=q,
        ),
    )
    report = verify_spec(spec, jt_pdf, use_llm_check=False)
    assert report.n_failed_high == 1
    assert should_retry(report) is True
    check = report.checks[0]
    assert check.verification_status == "not_found"
    assert check.failed is True


def test_verify_wrong_page_is_not_retry_trigger(jt_pdf):
    # Quote is verbatim on page 7, but spec claims page 12 — verified_wrong_page.
    q = SupportingQuote(text=JT_P7_VERBATIM, page=12)
    spec = _base_spec(
        signal=SignalSpec(
            name="past_return", formula="f", inputs=("close",),
            kind="past_return", lookback_months=6, skip_months=0,
            frequency="monthly",
            supporting_quote=q,
        ),
    )
    report = verify_spec(spec, jt_pdf, use_llm_check=False)
    # verified_wrong_page should be logged but NOT fail
    check = report.checks[0]
    assert check.verification_status == "verified_wrong_page"
    assert check.failed is False
    assert check.verified_page == 7
    assert should_retry(report) is False


def test_verify_no_quotes_produces_high_confidence_report(jt_pdf):
    # A spec with no supporting_quotes shouldn't fail — nothing to verify.
    spec = _base_spec()
    report = verify_spec(spec, jt_pdf, use_llm_check=False)
    assert report.n_checks == 0
    assert report.overall_confidence == "high"


def test_retry_feedback_mentions_field_and_claimed_page(jt_pdf):
    q = SupportingQuote(
        text="fabricated nonsense that is not in the paper",
        page=7,
    )
    spec = _base_spec(
        signal=SignalSpec(
            name="past_return", formula="f", inputs=("close",),
            kind="past_return", lookback_months=6, skip_months=0,
            frequency="monthly",
            supporting_quote=q,
        ),
    )
    report = verify_spec(spec, jt_pdf, use_llm_check=False)
    feedback = build_retry_feedback(report)
    assert "signal.supporting_quote" in feedback
    assert "not_found" in feedback or "FABRICATED" in feedback


# ---------------------------------------------------------------------------
# Multi-field, mixed severities
# ---------------------------------------------------------------------------

def test_verify_medium_severity_not_found_does_not_retry(jt_pdf):
    # universe is mapped to "medium" — a not_found there should NOT trigger retry.
    q = SupportingQuote(text="fabricated universe sentence", page=5)
    spec = _base_spec(
        universe=UniverseSpec(name="us", supporting_quote=q),
    )
    report = verify_spec(spec, jt_pdf, use_llm_check=False)
    assert report.n_failed_high == 0
    assert report.n_failed_medium == 1
    assert should_retry(report) is False


# ---------------------------------------------------------------------------
# LLM-marked: Haiku support check + end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.llm
def test_verify_haiku_flags_unsupporting_quote(jt_pdf):
    """The quote is verbatim from JT but is about ranking, not weighting.
    Haiku should flag supports='no' and the check should fail."""
    # Real quote from page 5 describing ranking
    q = SupportingQuote(
        text="the securities are ranked in ascending order on the basis of their returns",
        page=5,
    )
    # Attach it to the PORTFOLIO field whose claim is value-weighted
    spec = _base_spec(
        portfolio=PortfolioSpec(
            construction="decile", n_buckets=10, long_bucket=10,
            short_bucket=1, weighting="value", long_short=True,
            gross_exposure=2.0, supporting_quote=q,
        ),
    )
    report = verify_spec(spec, jt_pdf, use_llm_check=True)
    check = report.checks[0]
    assert check.verification_status in ("verified", "fuzzy_match")
    # Haiku should call this out as wrong-topic (quote is about ranking)
    assert check.support_check is not None
    if check.support_check.supports == "no":
        assert check.failed is True
        assert report.n_failed_high == 1


@pytest.mark.llm
def test_verify_spec_passes_on_real_a1_jt_output(jt_pdf):
    """End-to-end happy path: A1's cached extraction on JT should verify
    with overall_confidence >= medium and no high-severity failures."""
    from src.agents.extraction import extract_methodology

    spec = extract_methodology(jt_pdf)
    report = verify_spec(spec, jt_pdf, use_llm_check=True)
    assert report.n_failed_high == 0, (
        f"A1's extraction produced {report.n_failed_high} high-severity fails: "
        f"{[c.field_path for c in report.checks if c.failed]}"
    )
    assert report.overall_confidence in ("high", "medium")
