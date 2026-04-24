"""Phase 4 robustness battery — stress tests over a ReplicationSpec.

Each family rerunns the canonical engine with one parameter mutated. The
battery driver aggregates per-family results into a `RobustnessScorecard`.
D3 (Robustness Adversary) reads the scorecard and produces the narrative
judgment.
"""

from src.robustness.battery import ALL_FAMILIES, run_battery
from src.robustness.capacity import estimate_capacity
from src.robustness.costs import compute_cost_threshold_bps, run_cost_sweep
from src.robustness.data_quality import run_data_quality_check
from src.robustness.lag import compute_lag_half_life, run_lag_sweep
from src.robustness.liquidity import run_liquidity_sweep
from src.robustness.subperiod import run_subperiod_battery

__all__ = [
    "ALL_FAMILIES",
    "compute_cost_threshold_bps",
    "compute_lag_half_life",
    "estimate_capacity",
    "run_battery",
    "run_cost_sweep",
    "run_data_quality_check",
    "run_lag_sweep",
    "run_liquidity_sweep",
    "run_subperiod_battery",
]
