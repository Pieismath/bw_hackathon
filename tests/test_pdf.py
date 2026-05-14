"""Tests for src/pdf/ — parsing + quote verification on the JT 1993 PDF.

Quote verification is the single anti-hallucination guard rail for every
downstream extraction agent: when an agent claims the paper says X, we
verify X exists in the PDF before letting that claim flow downstream.
These tests exercise the guard rail on real text — ligature substitution,
unicode-dash swaps, and known-bad strings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pdf.parser import ParsedPDF, parse_pdf
from src.pdf.quote_verifier import VerificationResult, verify

JT_PDF = Path("data/papers/jegadeesh_titman_1993_returns_to_buying_winners_and_selling_losers.pdf")


@pytest.fixture(scope="module")
def jt() -> ParsedPDF:
    return parse_pdf(JT_PDF)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parser_opens_jt_and_counts_pages(jt: ParsedPDF):
    assert jt.n_pages == 28


def test_parser_get_text_by_page_returns_nonempty(jt: ParsedPDF):
    txt = jt.get_text_by_page(7)
    assert isinstance(txt, str)
    assert len(txt) > 500  # page 7 is dense with Table I


def test_parser_page_7_contains_table_i_header(jt: ParsedPDF):
    # Page 7 is the location of the headline Table I result.
    txt = jt.get_text_by_page(7)
    assert "Table I" in txt
    assert "Returns of Relative Strength Portfolios" in txt
    assert "January 1965 to December 1989" in txt


def test_parser_page_7_contains_both_panels(jt: ParsedPDF):
    txt = jt.get_text_by_page(7)
    assert "Panel A" in txt
    assert "Panel B" in txt


def test_parser_page_1_is_jstor_header(jt: ParsedPDF):
    # Page 1 is the JSTOR cover sheet with citation metadata.
    txt = jt.get_text_by_page(1)
    assert "JSTOR" in txt or "jstor" in txt.lower()


def test_parser_full_text_concatenates(jt: ParsedPDF):
    full = jt.full_text
    # Must be substantially larger than any single page, contain anchors
    # from multiple pages.
    assert "Returns of Relative Strength Portfolios" in full
    assert len(full) > 20_000


def test_parser_invalid_page_raises(jt: ParsedPDF):
    with pytest.raises((KeyError, IndexError, ValueError)):
        jt.get_text_by_page(0)
    with pytest.raises((KeyError, IndexError, ValueError)):
        jt.get_text_by_page(999)


def test_parser_get_tables_returns_structured_list(jt: ParsedPDF):
    # pdfplumber can't detect JT's spatially-laid-out tables (no borders),
    # so the list may be empty. The contract is: list of dicts with
    # {page: int, rows: list[list[str]]} — honest about what was extracted.
    tables = jt.get_tables()
    assert isinstance(tables, list)
    for t in tables:
        assert "page" in t and isinstance(t["page"], int)
        assert "rows" in t and isinstance(t["rows"], list)
        for row in t["rows"]:
            assert isinstance(row, list)


# ---------------------------------------------------------------------------
# Quote verifier — exact matching
# ---------------------------------------------------------------------------

def test_verify_returns_verified_result_type(jt: ParsedPDF):
    r = verify("Returns of Relative Strength Portfolios", jt)
    assert isinstance(r, VerificationResult)


def test_verify_known_good_quote_exact_match(jt: ParsedPDF):
    # Page-7-unique phrasing from Table I's caption. The shorter phrase
    # "Returns of Relative Strength Portfolios" appears earlier as a body
    # reference on page 5, which is a legitimate match but a different
    # page — this longer phrase is unambiguous.
    r = verify(
        "The relative strength portfolios are formed based on J-month lagged returns",
        jt,
    )
    assert r.status == "verified"
    assert r.found is True
    assert r.page == 7
    assert r.confidence == 1.0


def test_verify_multi_word_phrase_from_page_7(jt: ParsedPDF):
    r = verify("The sample period is January 1965 to December 1989.", jt)
    assert r.status == "verified"
    assert r.page == 7


def test_verify_returns_correct_page_not_adjacent(jt: ParsedPDF):
    # Verifier's claim: when a page-unique quote lives on page N, we
    # return page=N, not N-1 or N+1.
    r = verify("The sample period is January 1965 to December 1989.", jt)
    assert r.page == 7
    assert r.page != 6 and r.page != 8


# ---------------------------------------------------------------------------
# Quote verifier — negative cases
# ---------------------------------------------------------------------------

def test_verify_unknown_quote_returns_not_found(jt: ParsedPDF):
    r = verify("this sentence is definitely not in the paper anywhere", jt)
    assert r.status == "not_found"
    assert r.found is False
    assert r.page is None


def test_verify_empty_quote_rejected():
    # Empty quotes are meaningless; verifier should refuse rather than
    # silently "match" everything.
    with pytest.raises(ValueError):
        verify("", _make_empty_pdf())


def _make_empty_pdf() -> ParsedPDF:
    # Lightweight stub for the empty-quote validator
    return ParsedPDF(path=Path("/dev/null"), _pages_text={1: ""}, _tables=[])


# ---------------------------------------------------------------------------
# Quote verifier — normalization (fuzzy path)
# ---------------------------------------------------------------------------

def test_verify_handles_unicode_em_dash_substitution(jt: ParsedPDF):
    # Page 7 mentions "t-statistics" with ASCII hyphen. Substitute em-dash
    # — verifier must normalize both sides to match.
    quote = "The t\u2014statistics are reported in parentheses."
    r = verify(quote, jt)
    assert r.status in ("verified", "fuzzy_match")
    assert r.found is True
    assert r.page == 7


def test_verify_handles_ligature_substitution(jt: ParsedPDF):
    # "first column" on page 7 → substitute "fi" with the unicode ligature ﬁ.
    quote = "The values of J and K for the different strategies are indicated in the \uFB01rst column"
    r = verify(quote, jt)
    assert r.status in ("verified", "fuzzy_match")
    assert r.found is True
    assert r.page == 7


def test_verify_handles_collapsed_whitespace(jt: ParsedPDF):
    # Extra internal spaces + newlines should not defeat the verifier —
    # PDF text extraction introduces these at line breaks.
    quote = "Returns of   Relative\nStrength\tPortfolios"
    r = verify(quote, jt)
    assert r.status in ("verified", "fuzzy_match")
    assert r.found is True


def test_verify_fuzzy_threshold_rejects_garbled_text(jt: ParsedPDF):
    # Nonsensical text with enough english-shaped words that partial-ratio
    # could false-positive at a low threshold. At threshold 85 it should
    # still return not_found.
    quote = "Wolves devoured the ornate quantitative heritage of the balanced sentinel."
    r = verify(quote, jt, fuzzy_threshold=85)
    assert r.status == "not_found"


def test_verify_returns_confidence_in_unit_interval(jt: ParsedPDF):
    r = verify("Returns of Relative Strength Portfolios", jt)
    assert 0.0 <= r.confidence <= 1.0
    r2 = verify("this sentence is definitely not in the paper", jt)
    assert 0.0 <= r2.confidence <= 1.0


# ---------------------------------------------------------------------------
# Quote verifier — result structure
# ---------------------------------------------------------------------------

def test_verification_result_fields(jt: ParsedPDF):
    r = verify("Returns of Relative Strength Portfolios", jt)
    assert hasattr(r, "status")
    assert hasattr(r, "found")
    assert hasattr(r, "page")
    assert hasattr(r, "confidence")
    assert hasattr(r, "matched_text")
    assert r.status in ("verified", "fuzzy_match", "not_found")


def test_verification_result_immutable(jt: ParsedPDF):
    r = verify("Returns of Relative Strength Portfolios", jt)
    with pytest.raises((AttributeError, Exception)):
        r.status = "not_found"  # frozen dataclass / pydantic should reject


# ---------------------------------------------------------------------------
# Quote verifier — expected_page cross-check
# ---------------------------------------------------------------------------

def test_verify_expected_page_correct_exact_match(jt: ParsedPDF):
    # Agent claims page 7, and the quote is indeed on page 7 — verified.
    r = verify(
        "The values of J and K for the different strategies",
        jt,
        expected_page=7,
    )
    assert r.status == "verified"
    assert r.page == 7
    assert r.confidence == 1.0


def test_verify_expected_page_wrong_but_exact_match_elsewhere(jt: ParsedPDF):
    # Phrase is page-7-unique (verified empirically). Agent claims page 10.
    r = verify(
        "The values of J and K for the different strategies",
        jt,
        expected_page=10,
    )
    assert r.status == "verified_wrong_page"
    assert r.page == 7
    assert r.found is True
    assert r.confidence == 1.0


def test_verify_expected_page_out_of_range_low(jt: ParsedPDF):
    r = verify("anything", jt, expected_page=0)
    assert r.status == "page_out_of_range"
    assert r.found is False
    assert r.page is None


def test_verify_expected_page_out_of_range_high(jt: ParsedPDF):
    r = verify("anything", jt, expected_page=999)
    assert r.status == "page_out_of_range"
    assert r.found is False


def test_verify_expected_page_ligature_at_right_page(jt: ParsedPDF):
    # Ligature substitution at the correct page → verified via normalization.
    quote = "The values of J and K for the different strategies are indicated in the \uFB01rst column"
    r = verify(quote, jt, expected_page=7)
    assert r.status == "verified"
    assert r.page == 7


def test_verify_expected_page_fuzzy_on_right_page(jt: ParsedPDF):
    # Small corruption on the correct page — should classify as fuzzy_match,
    # not fuzzy_wrong_page (expected_page preference within the fuzzy tier).
    quote = "The sample peroid is January 1965 to December 1989."  # "peroid" typo
    r = verify(quote, jt, expected_page=7)
    assert r.status == "fuzzy_match"
    assert r.page == 7
    assert r.confidence < 1.0


def test_verify_expected_page_none_preserves_old_behavior(jt: ParsedPDF):
    # expected_page=None should produce identical result to omitting it.
    a = verify("The sample period is January 1965 to December 1989.", jt)
    b = verify("The sample period is January 1965 to December 1989.", jt, expected_page=None)
    assert a == b


def test_verify_exact_on_wrong_page_outranks_fuzzy_on_expected_page(jt: ParsedPDF):
    # The prompt locks this priority: exact-elsewhere > fuzzy-on-expected.
    # Use a phrase known to live ONLY on page 7, claim page 5. Result must
    # be verified_wrong_page (exact, page 7), not fuzzy_match on page 5.
    r = verify(
        "The values of J and K for the different strategies",
        jt,
        expected_page=5,
    )
    assert r.status == "verified_wrong_page"
    assert r.page == 7
