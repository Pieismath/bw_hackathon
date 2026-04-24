"""Validation agents: D1 (Result Comparator — this module), D2 (Divergence
Diagnostician — next session), D3 (Robustness Adversary — Phase 4)."""

from src.agents.validation.result_comparator import (
    DEFAULT_TOLERANCE,
    classify,
    compare_claim,
    compare_results_to_claims,
)

__all__ = [
    "DEFAULT_TOLERANCE",
    "classify",
    "compare_claim",
    "compare_results_to_claims",
]
