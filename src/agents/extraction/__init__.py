"""Extraction agents: A1 (Methodology Extractor) and A2 (Verifier)."""

from src.agents.extraction.methodology_extractor import (
    MODEL as METHODOLOGY_EXTRACTOR_MODEL,
    extract_methodology,
    format_pdf_as_user_content,
)
from src.agents.extraction.extraction_verifier import (
    HAIKU_MODEL as EXTRACTION_VERIFIER_MODEL,
    build_retry_feedback,
    extract_and_verify,
    haiku_support_check,
    should_retry,
    verify_spec,
)
from src.agents.extraction.adversarial_reviewer import (
    MODEL as ADVERSARIAL_REVIEWER_MODEL,
    fold_high_severity_into_spec,
    review,
)

__all__ = [
    "METHODOLOGY_EXTRACTOR_MODEL",
    "EXTRACTION_VERIFIER_MODEL",
    "ADVERSARIAL_REVIEWER_MODEL",
    "build_retry_feedback",
    "extract_and_verify",
    "extract_methodology",
    "fold_high_severity_into_spec",
    "format_pdf_as_user_content",
    "haiku_support_check",
    "review",
    "should_retry",
    "verify_spec",
]
