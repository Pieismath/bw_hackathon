"""Report template strings — stubs used before the full Phase 5 synthesizer.

Most of these are one-liners or small formatters. They live here so the
orchestration code doesn't hardcode strings, and so a future HTML/Jinja
synthesizer has a canonical source of truth for wording.
"""

from __future__ import annotations

import re

from src.specs import BacktestResult, ClaimComparison

# Data-quality: when the engine clips extreme returns, we surface the count.
# The BacktestResult's data_quality_flags already contain strings like:
#   "856 monthly returns exceeded ±500% and were clipped to NaN (thinly-
#    traded / data-artifact stocks)."
# This one-liner template is used when the report wants a standalone note
# rather than echoing the verbose engine-generated flag.
DATA_QUALITY_CLIP_NOTE_TEMPLATE = (
    "N={count} observations clipped at ±500% due to suspected data artifacts"
)


_CLIP_COUNT_RE = re.compile(r"(\d+)\s+monthly returns exceeded")


def format_data_quality_flags(result: BacktestResult) -> list[str]:
    """Translate the engine's raw data_quality_flags into human-readable notes.

    For the clip-count flag, rewrite it using DATA_QUALITY_CLIP_NOTE_TEMPLATE
    so downstream reports have a compact form. Other flags pass through
    unchanged.
    """
    out: list[str] = []
    for flag in result.data_quality_flags:
        match = _CLIP_COUNT_RE.search(flag)
        if match:
            count = int(match.group(1))
            out.append(DATA_QUALITY_CLIP_NOTE_TEMPLATE.format(count=count))
        else:
            out.append(flag)
    return out


def format_replication_gap_line(comp: ClaimComparison) -> str:
    """Single-line human-readable gap for one ClaimComparison."""
    direction = "same-sign" if comp.verdict != "opposite_sign" else "SIGN-FLIPPED"
    return (
        f"{comp.claim.metric}: paper {comp.claim.claimed_value*100:+.3f}%/mo, "
        f"ours {comp.replicated_value*100:+.3f}%/mo, "
        f"gap {comp.absolute_gap*100:+.3f}%/mo ({direction}) "
        f"→ verdict: {comp.verdict}"
    )
