"""PDF parsing + quote verification.

The parser produces a page-aware `ParsedPDF`; the verifier confirms that
extracted quotes actually appear in the PDF, tolerating PDF-extraction
artifacts (ligatures, unicode dashes, whitespace collapse).
"""

from src.pdf.parser import ParsedPDF, parse_pdf
from src.pdf.quote_verifier import (
    VerificationResult,
    VerificationStatus,
    normalize_for_match,
    verify,
)

__all__ = [
    "ParsedPDF",
    "VerificationResult",
    "VerificationStatus",
    "normalize_for_match",
    "parse_pdf",
    "verify",
]
