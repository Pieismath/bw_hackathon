"""A2 verification schemas.

The verifier walks every SupportingQuote in a ReplicationSpec, runs each
through src.pdf.quote_verifier.verify (deterministic) and a Haiku
support-check (LLM), and packages the results into a VerificationReport.

The `VerifiedReplicationSpec` is what downstream agents consume — it pairs
the extracted spec with the audit trail that justifies trusting it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.pdf.quote_verifier import VerificationStatus
from src.specs.claims import SupportingQuote
from src.specs.methodology import ReplicationSpec, SensitivityPriority

SupportStatus = Literal["yes", "no", "partial"]
OverallConfidence = Literal["high", "medium", "low"]


class SupportCheck(BaseModel):
    """Haiku's judgment on whether a quote's content supports the field's claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supports: SupportStatus
    reason: str = Field(min_length=1, max_length=400)


class QuoteVerification(BaseModel):
    """Full audit record for one SupportingQuote in the spec.

    `verification_status` is from the deterministic fuzzy verifier.
    `support_check` is present when the quote was found in the PDF (status
    in {verified, verified_wrong_page, fuzzy_match, fuzzy_wrong_page}) and
    None when it wasn't (we don't waste Haiku tokens on nonexistent text).
    `failed` rolls up whether this single check is acceptable for its
    severity tier; the overall retry decision aggregates across all checks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_path: str
    quote: SupportingQuote
    severity: SensitivityPriority

    verification_status: VerificationStatus
    verified_page: int | None
    verification_confidence: float = Field(ge=0.0, le=1.0)

    support_check: SupportCheck | None

    failed: bool
    failure_reason: str | None = None


class VerificationReport(BaseModel):
    """Aggregate report — what A2 emits for one spec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checks: tuple[QuoteVerification, ...]
    overall_confidence: OverallConfidence
    n_checks: int = Field(ge=0)
    n_failed_high: int = Field(ge=0)
    n_failed_medium: int = Field(ge=0)
    n_failed_low: int = Field(ge=0)
    retry_count: int = Field(ge=0, default=0)
    retry_feedback_history: tuple[str, ...] = ()


class VerifiedReplicationSpec(BaseModel):
    """A1's spec plus A2's audit trail. The pair downstream agents consume."""

    model_config = ConfigDict(extra="forbid")  # not frozen — orchestrator can attach notes

    spec: ReplicationSpec
    report: VerificationReport
