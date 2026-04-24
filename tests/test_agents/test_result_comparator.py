"""Tests for D1 — Result Comparator (deterministic, no LLM)."""

from __future__ import annotations

from datetime import date

import pytest

from src.agents.validation import (
    DEFAULT_TOLERANCE,
    classify,
    compare_claim,
)
from src.specs import PaperClaim, SupportingQuote


# ---------------------------------------------------------------------------
# Pure classifier
# ---------------------------------------------------------------------------

def test_classify_match_within_tolerance():
    # gap = 0.0005, tolerance = 0.001 → match
    assert classify(0.0095, 0.0100, tolerance=0.001) == "match"


def test_classify_partial_within_2x_tolerance():
    # gap = 0.0015, tolerance = 0.001 → partial
    assert classify(0.0095, 0.0110, tolerance=0.001) == "partial"


def test_classify_diverged_beyond_2x_same_sign():
    # gap = 0.0050, tolerance = 0.001 → diverged (same sign)
    assert classify(0.0095, 0.0200, tolerance=0.001) == "diverged"


def test_classify_opposite_sign():
    # claim positive, replicated negative → opposite_sign
    assert classify(0.0095, -0.0151, tolerance=0.001) == "opposite_sign"


def test_classify_zero_claim_not_opposite():
    # claim=0, replicated=0.01 — not opposite, just gap
    assert classify(0.0, 0.01, tolerance=0.001) == "diverged"


def test_classify_negative_claim_small_negative_result_match():
    # both negative and within tolerance → match
    assert classify(-0.0050, -0.0055, tolerance=0.001) == "match"


# ---------------------------------------------------------------------------
# compare_claim
# ---------------------------------------------------------------------------

def _jt_headline_claim() -> PaperClaim:
    return PaperClaim(
        claim_id="jt_hl_66_monthly",
        metric="long_short_monthly_return",
        claimed_value=0.0095,
        claimed_tstat=3.07,
        claimed_units="percent_per_month",
        paper_location="Table I Panel A, J=6 K=6 'Buy-sell' row",
        supporting_quote=SupportingQuote(text="Buy-sell 0.0095", page=7),
    )


def test_compare_claim_jt_opposite_sign():
    """Our end-to-end run on JT (Panel A no-skip on 1995-2020 data)
    produced -1.51%/mo. Compared to the +0.95%/mo claim → opposite_sign."""
    comp = compare_claim(_jt_headline_claim(), replicated_value=-0.01514)
    assert comp.verdict == "opposite_sign"
    assert comp.absolute_gap == pytest.approx(-0.01514 - 0.0095, rel=1e-6)


def test_compare_claim_carries_tstat_gap():
    comp = compare_claim(
        _jt_headline_claim(),
        replicated_value=-0.01514,
        replicated_tstat=-4.08,
    )
    assert comp.tstat_gap == pytest.approx(-4.08 - 3.07, rel=1e-6)


def test_compare_claim_relative_gap_nonzero():
    comp = compare_claim(_jt_headline_claim(), replicated_value=0.01)
    # rel gap ~ 5.3%
    assert comp.relative_gap == pytest.approx(0.01 / 0.0095 - 1.0, rel=1e-6)


def test_compare_claim_default_tolerance():
    # Confirm the exported DEFAULT_TOLERANCE is used when caller doesn't override
    comp = compare_claim(_jt_headline_claim(), replicated_value=0.0095 + DEFAULT_TOLERANCE / 2)
    assert comp.verdict == "match"
