"""Report synthesis — stubs for now; full templates + web UI are Phase 5.

Downstream synthesizers can compose the small building blocks here
rather than scattering format strings across callers.
"""

from src.report.templates import (
    DATA_QUALITY_CLIP_NOTE_TEMPLATE,
    format_data_quality_flags,
    format_replication_gap_line,
)

__all__ = [
    "DATA_QUALITY_CLIP_NOTE_TEMPLATE",
    "format_data_quality_flags",
    "format_replication_gap_line",
]
