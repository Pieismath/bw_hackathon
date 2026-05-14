"""PaperExtractionOverride — narrow, typed exit hatch for PDF-unrecoverable papers.

Some PDFs cannot be cleanly extracted by A1 even with a careful prompt:
the OCR layer is byte-scrambled (Lo & MacKinlay 1988), table cells are
stored with mirrored character order (Asness-Moskowitz-Pedersen 2013
Table I), or the relevant text simply isn't searchable at all. For these
cases, the right move is to capture a human-curated value AND bind it to
*textual evidence* the human transcribed, so the override:

  - silently disables if the PDF is replaced and the evidence span no
    longer matches (no risk of stale numbers leaking forward);
  - is visible in the bundle as ``verified_spec.applied_overrides`` so
    the UI banner can be honest about "manually curated value, not
    direct A1 extraction";
  - lives in ONE place (``data/extraction_overrides/*.yaml``) so adding,
    removing, or reviewing curated values is a small explicit operation
    rather than a search across the codebase.

Two papers in the active corpus currently need this mechanism:

  - ``lo_mackinlay_1988`` — PDF text stream is byte-scrambled; A1 cannot
    extract verifiable quotes for the methodology section. Override
    supplies a curated ``VarianceRatioStatistic`` headline_claim plus a
    curated ``VerificationReport`` for A2.
  - ``asness_moskowitz_pedersen_2013`` — Table I cells are stored with
    mirrored character order; A1 cannot recover the headline number.
    Override supplies a curated ``MonthlyLongShortReturn`` headline_claim.

A third paper (``jegadeesh_titman_1993``) carries a different kind of
override — ``universe.min_price = 10.0`` — for an industry-standard
penny-stock filter the paper is silent about. That one is keyed on
paper_id (in ``src/agents/extraction/extraction_verifier.py``'s
``PAPER_ID_OVERRIDES``) rather than on a quote checksum because there's
no in-paper evidence to bind it to. Both mechanisms coexist; future
papers should prefer ``PaperExtractionOverride`` when possible.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PaperExtractionOverrideKind = Literal[
    "headline_claim",
    "verification_report",
    "signal_direction",
    "universe_min_price",
]


class PaperExtractionOverride(BaseModel):
    """One curated patch to be applied to a paper's extraction.

    Bound to a verbatim-quote checksum, not to ``paper_id``: if the PDF is
    replaced and the evidence span no longer matches, the override silently
    disables. This is the structural guarantee that prevents stale numbers
    from leaking into future re-extractions.

    The ``override_value`` is intentionally typed as ``Any`` here because
    each ``kind`` carries a different payload shape (a ``HeadlineClaim``
    discriminated-union variant, a ``VerificationReport``, a string for
    ``signal_direction``, a float for ``universe_min_price``). The
    consumer (extraction_verifier.apply_paper_extraction_overrides)
    validates the payload against the expected Pydantic model for the
    given ``kind``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    paper_quote_checksum: str = Field(
        min_length=64, max_length=64,
        pattern=r"^[a-f0-9]{64}$",
        description=(
            "SHA-256 hex digest of the verbatim PDF quote the override is "
            "bound to. The verifier re-computes this digest by hashing the "
            "normalized parsed-PDF text containing the quote; if no match, "
            "the override silently disables."
        ),
    )
    paper_id_hint: str = Field(
        min_length=1, max_length=200,
        description=(
            "Documentation-only hint of which paper this override targets "
            "(e.g. ``'lo_mackinlay_1988'``). NOT used for matching — the "
            "quote checksum is the binding key. Present so a human reviewer "
            "can grep the override file by paper without resolving every "
            "checksum."
        ),
    )
    kind: PaperExtractionOverrideKind = Field(
        description=(
            "What the override patches. Determines how the consumer "
            "interprets ``override_value``."
        ),
    )
    field_path: str = Field(
        min_length=1, max_length=200,
        description=(
            "Dotted path of the spec field the override sets, e.g. "
            "``'headline_claim'`` (top-level) or "
            "``'signal.direction'`` (nested)."
        ),
    )
    override_value: Any = Field(
        description=(
            "Payload — shape depends on ``kind``. For ``kind='headline_claim'``, "
            "a dict that validates against the ``HeadlineClaim`` discriminated "
            "union. For ``kind='universe_min_price'``, a float. The consumer "
            "validates against the expected Pydantic model at apply time."
        ),
    )
    reason: str = Field(
        min_length=1, max_length=600,
        description=(
            "Human-readable explanation of WHY the override exists "
            "(what the PDF problem is, how the value was sourced). "
            "Surfaced in the UI banner so the curation is honest."
        ),
    )


def compute_quote_checksum(verbatim_text: str) -> str:
    """SHA-256 hex digest of a verbatim PDF span.

    Normalization policy is intentionally MINIMAL — we want false negatives
    (override disables on slight PDF changes) over false positives (override
    fires on the wrong paper). Only normalize:

      - leading / trailing whitespace stripped
      - internal whitespace collapsed to single spaces

    Do NOT normalize unicode, lowercase, fold ligatures, or expand em-dashes.
    Those transformations are the verifier's job at quote-match time; here
    we want maximum sensitivity to PDF differences.

    Use this to compute the ``paper_quote_checksum`` for a new override
    entry — read the verbatim text out of the PDF (via ``parse_pdf``),
    normalize identically, hash. Save the hex digest in the override file.
    """
    normalized = " ".join(verbatim_text.strip().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
