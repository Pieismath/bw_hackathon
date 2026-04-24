"""Quote verifier — the anti-hallucination guard rail.

Every claim an extraction agent emits carries a `SupportingQuote`. Before
downstream agents or the engine trust that claim, the verifier confirms
the quote actually appears in the PDF. Exact match is tried first; if it
fails we fall back to a rapidfuzz partial-ratio match with a configurable
threshold (default 85).

Normalization policy (applied to BOTH quote and PDF text before comparing):
  - Unicode ligatures expanded (ﬁ→fi, ﬂ→fl, ﬀ→ff, ﬃ→ffi, ﬄ→ffl)
  - Unicode dashes folded to ASCII hyphen (— – ‑ − → -)
  - Smart quotes folded to straight quotes (" " ' ' → " ')
  - Non-breaking space → regular space
  - Whitespace runs collapsed to a single space; leading/trailing trimmed
  - Case preserved (we don't lowercase)

Case preservation is deliberate: section headers and proper nouns carry
signal, and the fuzzy score at threshold 85 already tolerates the minor
OCR/case variance that actually occurs in our PDFs.

`SupportingQuote` (the producer side) intentionally does NOT apply any of
these transformations — it preserves the verbatim span from the extraction
agent so normalization rules can evolve here without invalidating stored
quotes. See src/specs/claims.py.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from src.pdf.parser import ParsedPDF

VerificationStatus = Literal[
    "verified",
    "fuzzy_match",
    "verified_wrong_page",
    "fuzzy_wrong_page",
    "page_out_of_range",
    "not_found",
]


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    found: bool
    page: int | None
    confidence: float
    matched_text: str | None


# Ligatures that appear in PDF text extraction (NFKD decomposes most; we
# list them explicitly so the mapping is reviewable).
_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}

# Unicode dashes / hyphens folded to ASCII '-'. Leaves minus sign alone
# inside math expressions? We fold it too — the verifier cares about
# textual equivalence, not math semantics.
_DASHES = {
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2015": "-",  # horizontal bar
    "\u2212": "-",  # minus
}

# Smart quotes → ASCII
_QUOTES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00ab": '"',
    "\u00bb": '"',
}

_NBSP = "\u00a0"


def normalize_for_match(text: str) -> str:
    """Apply the full normalization pipeline (see module docstring)."""
    # NFKC first — expands most compatibility forms (incl. many ligatures).
    out = unicodedata.normalize("NFKC", text)
    # Explicit maps for anything NFKC missed or didn't cover the way we want.
    for src, dst in _LIGATURES.items():
        out = out.replace(src, dst)
    for src, dst in _DASHES.items():
        out = out.replace(src, dst)
    for src, dst in _QUOTES.items():
        out = out.replace(src, dst)
    out = out.replace(_NBSP, " ")
    # Collapse whitespace runs to one space, strip leading/trailing.
    out = re.sub(r"\s+", " ", out).strip()
    return out


def verify(
    quote: str,
    pdf: ParsedPDF,
    fuzzy_threshold: int = 85,
    expected_page: int | None = None,
) -> VerificationResult:
    """Check whether `quote` appears in `pdf`.

    When `expected_page` is None, behavior is: exact match anywhere wins,
    fuzzy match anywhere next, else not_found.

    When `expected_page` is supplied (1-indexed), the priority becomes:
      1. Exact at `expected_page`           → verified
      2. Exact on a DIFFERENT page          → verified_wrong_page
      3. Fuzzy at `expected_page` >= thresh → fuzzy_match
      4. Fuzzy on a DIFFERENT page >= thresh → fuzzy_wrong_page
      5. Nothing above threshold            → not_found
    If `expected_page` is outside [1, n_pages], returns page_out_of_range
    without scanning.

    Exact-on-wrong-page outranks fuzzy-on-expected-page: strong evidence
    the content exists verbatim elsewhere is more actionable than weak
    evidence at the claimed page.
    """
    if not quote or not quote.strip():
        raise ValueError("quote must be non-empty")

    if expected_page is not None and (
        expected_page < 1 or expected_page > pdf.n_pages
    ):
        return VerificationResult(
            status="page_out_of_range",
            found=False,
            page=None,
            confidence=0.0,
            matched_text=None,
        )

    q = normalize_for_match(quote)

    # Pre-normalize each page once so the four subsequent scans don't repeat work.
    norm_by_page: list[tuple[int, str]] = [
        (p, normalize_for_match(raw)) for p, raw in pdf.iter_pages()
    ]

    # --- Tier 1: exact match at expected_page (if given) ----------------
    if expected_page is not None:
        exp_text = dict(norm_by_page).get(expected_page, "")
        if q in exp_text:
            return VerificationResult(
                status="verified",
                found=True,
                page=expected_page,
                confidence=1.0,
                matched_text=q,
            )

    # --- Tier 2: exact match anywhere (elsewhere, if expected_page set) ---
    for page_num, page_norm in norm_by_page:
        if expected_page is not None and page_num == expected_page:
            continue
        if q in page_norm:
            status: VerificationStatus = (
                "verified_wrong_page" if expected_page is not None else "verified"
            )
            return VerificationResult(
                status=status,
                found=True,
                page=page_num,
                confidence=1.0,
                matched_text=q,
            )

    # --- Tier 3: fuzzy at expected_page ---------------------------------
    if expected_page is not None:
        exp_text = dict(norm_by_page).get(expected_page, "")
        if exp_text:
            score = fuzz.partial_ratio(q, exp_text)
            if score >= fuzzy_threshold:
                return VerificationResult(
                    status="fuzzy_match",
                    found=True,
                    page=expected_page,
                    confidence=score / 100.0,
                    matched_text=None,
                )

    # --- Tier 4: fuzzy elsewhere (or anywhere, if expected_page None) ---
    best_page: int | None = None
    best_score: float = 0.0
    for page_num, page_norm in norm_by_page:
        if expected_page is not None and page_num == expected_page:
            continue
        if not page_norm:
            continue
        score = fuzz.partial_ratio(q, page_norm)
        if score > best_score:
            best_score = score
            best_page = page_num

    confidence = best_score / 100.0
    if best_score >= fuzzy_threshold and best_page is not None:
        status = "fuzzy_wrong_page" if expected_page is not None else "fuzzy_match"
        return VerificationResult(
            status=status,
            found=True,
            page=best_page,
            confidence=confidence,
            matched_text=None,
        )

    return VerificationResult(
        status="not_found",
        found=False,
        page=None,
        confidence=confidence,
        matched_text=None,
    )
