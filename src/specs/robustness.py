"""Phase 4 schemas: stress-test results, robustness scorecard, and D3's judgment.

The scorecard is the deterministic output of the robustness battery (Step 11).
The judgment is D3's structured narrative on top (Step 12 + 12.5).

Design choices:
- StressTestResult intentionally does NOT carry the full BacktestResult — that
  would bloat scorecard JSONs by 10x for a 12-test battery. The full result
  is recoverable from the engine's spec_hash cache if needed.
- We classify signal_type from the lag-sweep half-life rather than from a
  daily-return decay profile. Coarser but already informative; the fully
  daily-resolved version is Phase 5.5 work.
- gap_attribution makes the system explicit about whether residual replication
  gaps are data limitations, methodology fragility, post-publication decay,
  or unexplained. Mentor priority.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

StressTestFamily = Literal[
    "subperiod",
    "costs",
    "lag",
    "capacity",
    "liquidity",
    "data_quality",
]

SignalType = Literal[
    "structural",         # alpha persists for >30 days
    "information_based",  # decays over 5–30 days
    "microstructure",     # decays in 1–5 days (bid-ask, reversal)
    "stale",              # alpha negative or undefined at all lags
    "not_evaluated",      # didn't run lag sweep
]

GapAttribution = Literal[
    "data_limitation",          # different data source / coverage / sample
    "methodology_fragility",    # paper's choices break under realistic stress
    "post_publication_decay",   # signal worked in paper era, faded after
    "unexplained",
]


class StressTestResult(BaseModel):
    """One row in the robustness scorecard. One stress test's outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    family: StressTestFamily
    parameter_swept: dict[str, Any]
    headline_metric: float          # signed mean monthly return (or alpha)
    headline_tstat: float | None
    n_periods: int = Field(ge=0)
    surviving: bool                  # passed: alpha > 0 AND tstat > 1.96
    notes: str = ""
    spec_hash: str | None = None     # for engine-cache lookup of the full result


class RobustnessScorecard(BaseModel):
    """Step 11 output. Pure code; no LLM."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_mean_return: float
    baseline_tstat: float
    baseline_n_periods: int = Field(ge=0)
    tests: tuple[StressTestResult, ...]
    n_tests: int = Field(ge=0)
    n_surviving: int = Field(ge=0)
    families_run: tuple[StressTestFamily, ...]
    fragility_signals: tuple[str, ...] = ()
    cost_threshold_bps: float | None = None       # bps where alpha crosses zero
    lag_half_life_days: float | None = None       # lag where alpha falls 50%
    capacity_estimate_usd: float | None = None    # AUM at 50bps impact


class RobustnessJudgment(BaseModel):
    """Step 12 output. D3's narrative on top of the scorecard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    surviving_count: int = Field(ge=0)
    n_tests: int = Field(ge=0)
    fragility_signals: tuple[str, ...]
    implementable_alpha: float
    implementable_alpha_basis: str = Field(min_length=1, max_length=400)
    signal_type: SignalType
    capacity_estimate_usd: float | None = None
    primary_failure_modes: tuple[str, ...] = Field(min_length=0, max_length=5)

    # Gap attribution (Step 12.5 — mentor priority)
    gap_attribution: GapAttribution
    gap_attribution_evidence: str = Field(min_length=1, max_length=600)

    confidence: Literal["high", "medium", "low"]
    summary: str = Field(min_length=1, max_length=1500)
