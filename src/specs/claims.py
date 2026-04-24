"""Claims the paper makes and how we compare replicated results to them.

SupportingQuote tethers every methodology field and every claim back to a
verbatim span in the PDF. The quote verifier (src/pdf/quote_verifier.py) fills
in `verified=True` + `match_confidence` before downstream agents may consume.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ClaimVerdict = Literal["match", "close", "divergence", "failure"]


class SupportingQuote(BaseModel):
    """A verbatim span from the source paper with its page number.

    Normalization policy — IMPORTANT: producers MUST preserve the exact
    character sequence from the PDF. No lowercasing, no unicode folding
    (em-dash ≠ hyphen), no internal whitespace collapsing, no ligature
    expansion. The only transformation this model applies is stripping
    leading/trailing whitespace, which the quote verifier's fuzzy match
    treats as insignificant anyway.

    The verifier (`src.pdf.quote_verifier`) owns all normalization: it
    normalizes both the extracted PDF text *and* the stored quote text
    identically (via the same routine) before comparing. Keeping this
    model verbatim means we can change verifier normalization later
    without invalidating stored quotes.

    `verified` is False until the quote verifier confirms the text exists in
    the PDF (via exact or fuzzy match). Downstream agents must not trust an
    unverified quote.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=400)
    page: int = Field(ge=1)
    match_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    verified: bool = False

    @field_validator("text")
    @classmethod
    def _strip_and_require_content(cls, v: str) -> str:
        # Strip outer whitespace only. DO NOT normalize unicode, collapse
        # internal whitespace, or lowercase — see class docstring.
        stripped = v.strip()
        if not stripped:
            raise ValueError("quote text cannot be whitespace-only")
        # 400-char ceiling enforced at the extractor prompt contract:
        # short quotes (1 sentence, 2 max) match the PDF more reliably
        # and force the extractor to pick the most informative span.
        if len(stripped) > 400:
            raise ValueError(
                f"quote text exceeds 400 characters ({len(stripped)}); "
                "shorten to one sentence (two max)"
            )
        return stripped


class PaperClaim(BaseModel):
    """A single quantitative claim the paper makes, with its source quote.

    These are the targets of replication. Each claim is compared against an
    engine-computed value via ClaimComparison.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    metric: str
    claimed_value: float
    claimed_tstat: float | None = None
    claimed_units: str
    paper_location: str
    supporting_quote: SupportingQuote
    context: str | None = None


class ClaimComparison(BaseModel):
    """Outcome of comparing a replicated backtest result to a paper claim.

    Tolerance is metric-dependent — e.g. for Novy-Marx monthly long-short
    returns we use ±0.1% per month.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: PaperClaim
    replicated_value: float
    absolute_gap: float
    relative_gap: float
    tstat_gap: float | None = None
    tolerance_used: float
    verdict: ClaimVerdict
    notes: str = ""
