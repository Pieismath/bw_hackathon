"""Tests for A1 — Methodology Extractor.

Unit tests (offline): the PDF→user-content formatter and prompt loading.
Integration (llm-marked, cached): run A1 against the JT 1993 PDF and
validate the extracted spec's shape and key fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.extraction import (
    METHODOLOGY_EXTRACTOR_MODEL,
    extract_methodology,
    format_pdf_as_user_content,
)
from src.pdf.parser import ParsedPDF, parse_pdf
from src.specs import ReplicationSpec

JT_PDF = Path("data/papers/jegadeesh_titman_1993_momentum.pdf")


# ---------------------------------------------------------------------------
# Unit: formatter
# ---------------------------------------------------------------------------

def test_format_pdf_emits_page_tags_in_order():
    pdf = ParsedPDF(
        path=Path("/dev/null"),
        _pages_text={1: "alpha", 2: "beta", 3: "gamma"},
    )
    out = format_pdf_as_user_content(pdf)
    assert "[Page 1]\nalpha" in out
    assert "[Page 2]\nbeta" in out
    assert "[Page 3]\ngamma" in out
    # Order preserved
    assert out.index("[Page 1]") < out.index("[Page 2]") < out.index("[Page 3]")


def test_format_pdf_empty_pages_still_emit_tag():
    pdf = ParsedPDF(
        path=Path("/dev/null"),
        _pages_text={1: "text", 2: "", 3: "more"},
    )
    out = format_pdf_as_user_content(pdf)
    assert "[Page 2]" in out


def test_format_pdf_strips_trailing_whitespace_per_page():
    # Trailing whitespace per page should not bleed into the next page's tag.
    pdf = ParsedPDF(
        path=Path("/dev/null"),
        _pages_text={1: "body  \n   ", 2: "next"},
    )
    out = format_pdf_as_user_content(pdf)
    assert "body\n\n[Page 2]" in out


# ---------------------------------------------------------------------------
# Configuration sanity
# ---------------------------------------------------------------------------

def test_extractor_uses_opus_47():
    # The spec locks Opus 4.7 for A1. Downgrading to Haiku would reduce
    # reading comprehension on 28-page papers — catch in CI.
    assert METHODOLOGY_EXTRACTOR_MODEL == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Integration: A1 on the JT PDF (cached after first run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def jt() -> ParsedPDF:
    return parse_pdf(JT_PDF)


@pytest.mark.llm
def test_a1_returns_replication_spec(jt: ParsedPDF):
    spec = extract_methodology(jt)
    assert isinstance(spec, ReplicationSpec)
    assert spec.paper_id  # non-empty
    assert spec.paper_title  # non-empty


@pytest.mark.llm
def test_a1_jt_signal_is_past_return(jt: ParsedPDF):
    spec = extract_methodology(jt)
    assert spec.signal.kind == "past_return"
    assert spec.signal.lookback_months is not None
    assert spec.signal.lookback_months >= 3
    assert spec.signal.lookback_months <= 12


@pytest.mark.llm
def test_a1_jt_portfolio_is_decile_long_short(jt: ParsedPDF):
    spec = extract_methodology(jt)
    assert spec.portfolio.long_short is True
    assert spec.portfolio.short_bucket is not None
    # JT uses deciles (top/bottom 10%)
    assert spec.portfolio.n_buckets in (10, 5)


@pytest.mark.llm
def test_a1_jt_rebalance_is_monthly(jt: ParsedPDF):
    spec = extract_methodology(jt)
    assert spec.rebalance.frequency == "monthly"


@pytest.mark.llm
def test_a1_jt_raises_execution_lag_ambiguity(jt: ParsedPDF):
    # JT (1993) does not specify the formation-to-trade delay. The prompt
    # makes execution_lag_days a mandatory high-severity flag when silent.
    spec = extract_methodology(jt)
    params = {f.parameter for f in spec.ambiguities}
    assert any("execution_lag" in p for p in params), (
        f"A1 failed to flag execution_lag_days; flagged params were {params}"
    )


@pytest.mark.llm
def test_a1_jt_quotes_under_400_chars(jt: ParsedPDF):
    # Schema enforces this — the test guards against silent regressions.
    spec = extract_methodology(jt)
    for field_name in ("universe", "signal", "portfolio", "rebalance"):
        quote = getattr(spec, field_name).supporting_quote
        if quote is not None:
            assert len(quote.text) <= 400, (
                f"{field_name}.supporting_quote too long: {len(quote.text)} chars"
            )
