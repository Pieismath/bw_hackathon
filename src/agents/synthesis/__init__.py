"""Phase 5 synthesis layer.

E1 (`report_synthesizer`) aggregates the cached Phase 1-4 artifacts into a
single `report.json` payload that the UI loads on page load. No LLM calls
— the narratives are already written by D2 / D3; E1 just normalizes and
joins them so the frontend has one fetch and one schema.
"""

from src.agents.synthesis.report_synthesizer import (
    REQUIRED_INPUTS,
    OPTIONAL_INPUTS,
    build_report,
    write_report,
)

__all__ = [
    "REQUIRED_INPUTS",
    "OPTIONAL_INPUTS",
    "build_report",
    "write_report",
]
