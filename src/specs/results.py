"""BacktestResult + ReplicationResult schemas.

BacktestResult is what the engine emits from a single ReplicationSpec run;
it is frozen and immutable by design. ReplicationResult is the orchestrator's
working bundle — it accumulates fields (ambiguity diagnostics, robustness
scorecard, implementable alpha) as later pipeline stages complete.

Because the mutable builder is exactly where spec pollution can sneak in,
the class exposes `finalize()` which returns a `FinalizedReplicationResult`
that is frozen. The report synthesizer and web UI consume the frozen variant
only — if provenance is to mean anything, downstream consumers must not be
able to re-render the same record with different numbers.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.specs.claims import ClaimComparison, PaperClaim
from src.specs.methodology import AmbiguityFlag, ReplicationSpec
from src.specs.provenance import ProvenanceRecord

OverallConfidence = Literal["high", "medium", "low"]
ReturnConvention = Literal["arithmetic_monthly", "log_monthly", "arithmetic_daily"]


SpecAdaptationKind = Literal[
    "signal_kind_proxy_substitution",      # unsupported signal.kind → past_return proxy
    "lookback_clamp",                      # signal.lookback_months clamped to fit data panel
    "weighting_fallback",                  # portfolio.weighting='signal_weighted' → 'equal'
    "portfolio_bucket_coercion",           # n_buckets coerced (e.g. quintile → tercile on 6-factor universe)
    "engine_does_not_expose_per_leg",      # claim comparison requires per-leg returns the engine doesn't emit
    "fundamentals_unavailable_in_window",  # fundamental_ratio spec outside fundamental data coverage
    "unknown_fundamental_ratio",           # fundamental_ratio name not in registry; fell back to proxy
    "out_of_sample_engine_window",         # paper window outside data panel; ran OOS
    "window_clipped_to_panel",             # paper window partially clipped to data panel
]


class SpecAdaptation(BaseModel):
    """Typed record of a substitution the engine made because the literal
    spec was infeasible against the available data / engine capabilities.

    Replaces the earlier string-flag idiom (e.g. ``"engine fallback: signal.kind=..."``
    appended to ``data_quality_flags``) that downstream consumers had to
    grep. Each entry carries the field that was changed, the original
    value, the substituted value, and a free-text reason.

    A frontend like ``app/static/app.js``'s ``detectProxyMode`` becomes a
    one-liner that checks the typed field instead of substring-matching
    ``data_quality_flags`` for an English sentence.

    String fields ``from_value`` / ``to_value`` are stringified
    representations of arbitrary spec values (e.g. ``"variance_ratio"``,
    ``"signal_weighted"``, ``"360"``, ``"None"``) — they're for display
    and provenance, not for round-tripping back into a spec. The
    machine-readable substitution is already in the spec itself; this
    record exists to make the WHY honest in the report.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SpecAdaptationKind
    field_path: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Dotted path of the spec field that was substituted "
            "(e.g. ``'signal.kind'``, ``'portfolio.weighting'``, "
            "``'signal.lookback_months'``)."
        ),
    )
    from_value: str | None = Field(
        default=None,
        max_length=200,
        description="Stringified original value, or None if the field was unset.",
    )
    to_value: str | None = Field(
        default=None,
        max_length=200,
        description="Stringified substituted value, or None if no replacement (e.g. for documentation-only adaptations).",
    )
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Why the substitution was applied; surfaced in the UI banner.",
    )


class ReturnObservation(BaseModel):
    """One period's return, tagged with the rebalance cycle it belongs to.

    `period_end` is when the return was measured. `formation_date` is the
    date the portfolio was actually formed — multiple daily observations
    inside a single holding period share one formation_date, which is how
    the decay analyzer groups them. For monthly-frequency rebalances with
    no intra-period tracking, formation_date equals period_start (often
    inferable but we still carry it).

    `rebalance_id` is an optional short tag (e.g. "2005-03" or "cycle_042")
    for human-readable grouping in reports.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    period_end: date
    ret: float
    formation_date: date | None = None
    rebalance_id: str | None = None
    long_ret: float | None = None
    short_ret: float | None = None
    turnover: float | None = None


class SubperiodSummary(BaseModel):
    """Summary stats for a subperiod of a backtest (no nested time series)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    start_date: date
    end_date: date
    n_periods: int = Field(ge=0)
    mean_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    alpha: float
    alpha_tstat: float
    max_drawdown: float


class FormationDecayPoint(BaseModel):
    """Average per-tranche return at a given age-since-formation.

    For a K-month holding strategy, a tranche formed at month M contributes
    to measurement months {M+1, ..., M+K}. `age_months=1` is the first
    holding month after formation; `age_months=K` is the last. This decay
    curve — mean tranche return grouped by age across the full sample —
    answers the trader's "how long is the signal alive?" question directly.

    Numbers are gross (no transaction-cost amortization) because the cost
    is a constant shift across ages and obscures the signal shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    age_months: int = Field(ge=1)
    mean_ret: float
    std_error: float = Field(ge=0.0)
    n_observations: int = Field(ge=0)


class BacktestResult(BaseModel):
    """Engine output for a single ReplicationSpec run.

    Time series live in `returns` (always) and `daily_returns` (only when
    the spec requested daily tracking). Summary stats are derived from
    `returns` and frozen here so downstream consumers don't need to
    recompute them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_hash: str
    start_date: date
    end_date: date
    n_periods: int = Field(ge=0)
    returns: tuple[ReturnObservation, ...]
    daily_returns: tuple[ReturnObservation, ...] | None = None
    mean_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    alpha: float
    alpha_annualized: float
    alpha_tstat: float
    beta: float | None = None
    turnover: float = Field(ge=0.0)
    max_drawdown: float
    hit_rate: float = Field(ge=0.0, le=1.0)
    transaction_cost_bps: float = Field(default=0.0, ge=0.0)
    return_convention: ReturnConvention = "arithmetic_monthly"
    newey_west_lag: int = Field(default=0, ge=0)
    subperiod_summaries: tuple[SubperiodSummary, ...] = ()
    # Mean per-tranche return by months-since-formation; empty when the
    # engine didn't produce any overlapping-tranche data. Entries are
    # ordered by ascending age_months.
    decay_by_age: tuple[FormationDecayPoint, ...] = ()
    warnings: tuple[str, ...] = ()
    data_quality_flags: tuple[str, ...] = ()
    # Typed prep-time substitutions (Phase E). Each SpecAdaptation captures
    # one infeasibility the engine layer worked around (kind proxy,
    # lookback clamp, OOS window substitution, etc.). Frontend / D2 / D3
    # dispatch on these rather than substring-matching data_quality_flags.
    # Empty when the spec ran verbatim with no substitutions.
    spec_adaptations: tuple["SpecAdaptation", ...] = ()
    provenance: ProvenanceRecord

    @model_validator(mode="after")
    def _check_lengths(self) -> "BacktestResult":
        if len(self.returns) != self.n_periods:
            raise ValueError(
                f"n_periods {self.n_periods} does not match returns length {len(self.returns)}"
            )
        if self.end_date <= self.start_date:
            raise ValueError(
                f"end_date {self.end_date} must be after start_date {self.start_date}"
            )
        return self

    def to_dataframe(self, kind: Literal["returns", "daily"] = "returns") -> pd.DataFrame:
        """Materialize the requested return series as a pandas DataFrame.

        Columns: period_end, ret, formation_date, rebalance_id, long_ret,
        short_ret, turnover. Downstream metrics / robustness code should
        go through this helper rather than re-walking the tuple.
        """
        if kind == "returns":
            series = self.returns
        elif kind == "daily":
            if self.daily_returns is None:
                raise ValueError(
                    "daily_returns is None; spec did not enable measure_daily_returns"
                )
            series = self.daily_returns
        else:
            raise ValueError(f"unknown kind={kind!r}")
        if not series:
            return pd.DataFrame(
                columns=[
                    "period_end", "ret", "formation_date", "rebalance_id",
                    "long_ret", "short_ret", "turnover",
                ]
            )
        return pd.DataFrame([obs.model_dump() for obs in series])


class AmbiguityDiagnostic(BaseModel):
    """How much of a replication gap is driven by a single ambiguity choice.

    Produced by the Divergence Diagnostician: it mutates the spec's
    default_chosen to each alternative, reruns the engine, and records the
    delta. The ambiguity with the largest delta is the dominant driver.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ambiguity: AmbiguityFlag
    alternative_tried: str
    replicated_metric: str
    baseline_value: float
    alternative_value: float
    delta: float
    closes_gap: bool
    notes: str = ""


class _ReplicationResultFields(BaseModel):
    """Shared field definitions for the mutable builder and the frozen final.

    The only difference between `ReplicationResult` and
    `FinalizedReplicationResult` is the model_config — defining fields once
    here prevents drift between the two.
    """

    replication_id: str
    spec: ReplicationSpec
    result: BacktestResult
    claims: tuple[PaperClaim, ...]
    comparisons: tuple[ClaimComparison, ...]
    ambiguity_diagnostics: tuple[AmbiguityDiagnostic, ...] = ()
    robustness_scorecard: dict[str, float] | None = None
    implementable_alpha: float | None = None
    overall_confidence: OverallConfidence = "medium"
    notes: tuple[str, ...] = ()
    provenance: ProvenanceRecord


class ReplicationResult(_ReplicationResultFields):
    """Mutable builder used by the orchestrator as pipeline stages complete.

    Fields with None defaults (`robustness_scorecard`, `implementable_alpha`)
    are intentionally nullable — the orchestrator fills them in Phase 4.
    Call `finalize()` once all stages are done; consumers (report synthesizer,
    web UI) must accept only `FinalizedReplicationResult`.
    """

    model_config = ConfigDict(extra="forbid")

    def finalize(self) -> "FinalizedReplicationResult":
        return FinalizedReplicationResult.model_validate(self.model_dump())


class FinalizedReplicationResult(_ReplicationResultFields):
    """Immutable snapshot of a completed replication. Reports and UI use this.

    Frozen so that downstream code can't inadvertently re-render the same
    replication with different numbers — provenance IDs that point to this
    record must remain truthful over time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
