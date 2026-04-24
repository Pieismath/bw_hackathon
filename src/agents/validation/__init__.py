"""Validation agents: D1 (Result Comparator), D2 (Divergence Diagnostician),
D3 (Robustness Adversary — Phase 4)."""

from src.agents.validation.divergence_diagnostician import (
    EARLY_EXIT_GAP_THRESHOLD,
    MAX_EXPERIMENTS_DEFAULT,
    MODEL as DIVERGENCE_DIAGNOSTICIAN_MODEL,
    apply_mutation,
    diagnose,
    read_current_value,
    run_backtest_cached,
)
from src.agents.validation.result_comparator import (
    DEFAULT_TOLERANCE,
    classify,
    compare_claim,
    compare_results_to_claims,
)
from src.agents.validation.robustness_adversary import (
    MODEL as ROBUSTNESS_ADVERSARY_MODEL,
    judge,
)

__all__ = [
    "DEFAULT_TOLERANCE",
    "DIVERGENCE_DIAGNOSTICIAN_MODEL",
    "EARLY_EXIT_GAP_THRESHOLD",
    "MAX_EXPERIMENTS_DEFAULT",
    "ROBUSTNESS_ADVERSARY_MODEL",
    "apply_mutation",
    "classify",
    "compare_claim",
    "compare_results_to_claims",
    "diagnose",
    "judge",
    "read_current_value",
    "run_backtest_cached",
]
