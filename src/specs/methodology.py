"""ReplicationSpec and its component specs.

ReplicationSpec is the central structured contract between the extraction
agents (who read papers) and the backtest engine (which executes them). The
engine knows nothing about LLMs; it runs whatever spec it's given. That's
the decoupling that lets one tested engine serve every replication.

Every field that is not literally written in the paper must be declared as
an AmbiguityFlag. Ambiguities are the product, not a bug — they drive the
sensitivity analysis downstream.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.specs.claims import SupportingQuote
from src.specs.paper_metrics import HeadlineClaim

SensitivityPriority = Literal["high", "medium", "low"]

Region = Literal["US", "global", "developed", "emerging", "custom"]
AssetClass = Literal["equity", "bond", "fx", "commodity", "macro", "mixed"]

SignalDirection = Literal["long_high", "long_low"]
SignalFrequency = Literal["daily", "weekly", "monthly", "quarterly", "annual"]
SignalKind = Literal["past_return", "variance_ratio", "fundamental_ratio", "custom"]

Construction = Literal["quintile", "decile", "tercile", "custom_sort"]
Weighting = Literal["equal", "value", "signal_weighted"]

Frequency = Literal["daily", "weekly", "monthly", "quarterly", "annual"]
PriceConvention = Literal["close", "open"]
ExecutionConvention = Literal["close", "open", "vwap"]


class AmbiguityFlag(BaseModel):
    """A methodological choice the paper did not specify unambiguously.

    Every ambiguity the system resolves by default must be recorded here so
    the sensitivity analyzer can rerun the backtest with alternatives and
    quantify how much of any gap is driven by each choice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    parameter: str
    default_chosen: str
    alternatives: tuple[str, ...] = ()
    paper_evidence: SupportingQuote | None = None
    sensitivity_priority: SensitivityPriority = "medium"
    reason: str


class UniverseSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    region: Region = "US"
    asset_class: AssetClass = "equity"
    include_filters: tuple[str, ...] = ()
    exclude_filters: tuple[str, ...] = ()
    min_price: float | None = None
    min_market_cap_usd: float | None = None
    exchanges: tuple[str, ...] = ()
    supporting_quote: SupportingQuote | None = None


class SignalSpec(BaseModel):
    """The formula that produces a per-name score at each rebalance date.

    For `kind="past_return"` (momentum / reversal signals), the engine
    computes a cross-sectional ranking based on cumulative return over
    the window `[t - (lookback_months + skip_months), t - skip_months]`.
    The skip portion is the "signal skip" that keeps the most recent month
    out of the signal — it is part of the signal definition and lives
    here, not in RebalanceSpec. RebalanceSpec.execution_lag_days is
    something different: it controls when the already-formed portfolio
    trades relative to formation date. The two axes are orthogonal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    formula: str
    inputs: tuple[str, ...]
    transformations: tuple[str, ...] = ()
    direction: SignalDirection = "long_high"
    frequency: SignalFrequency = "monthly"
    lag_fundamentals_days: int = Field(default=0, ge=0)
    kind: SignalKind = "custom"
    lookback_months: int | None = Field(default=None, ge=1)
    skip_months: int = Field(default=0, ge=0)
    supporting_quote: SupportingQuote | None = None

    @model_validator(mode="after")
    def _check_past_return_fields(self) -> "SignalSpec":
        if self.kind == "past_return" and self.lookback_months is None:
            raise ValueError(
                "kind='past_return' requires lookback_months to be set"
            )
        if self.kind == "variance_ratio":
            # VR needs enough history to estimate both variances. The TRUE
            # statistical minimum is ~36-60 months (the AQR paper uses an
            # expanding 30+ year window). The validator's job here is just
            # to keep obvious garbage out — A1 frequently extracts the
            # paper's q=12 aggregation parameter into this field by
            # mistake, so we accept >=12 here and let the engine layer's
            # `_engine_kind_fallback` bump anything <24 to 60 with an
            # honest data_quality_flag. Strictness here would crash the
            # endpoint before fallback can fix the value.
            if self.lookback_months is None:
                raise ValueError(
                    "kind='variance_ratio' requires lookback_months to be set"
                )
            if self.lookback_months < 12:
                raise ValueError(
                    "kind='variance_ratio' requires lookback_months >= 12 "
                    "(at minimum one annual cycle to compute rolling-12m "
                    "returns; the engine will auto-bump small values to 60)"
                )
        return self


class PortfolioSpec(BaseModel):
    """How a signal becomes a portfolio."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    construction: Construction = "quintile"
    n_buckets: int = Field(default=5, ge=2, le=20)
    long_bucket: int = Field(ge=1)
    short_bucket: int | None = None
    weighting: Weighting = "value"
    use_nyse_breakpoints: bool = False
    long_short: bool = True
    gross_exposure: float = Field(default=2.0, gt=0)
    supporting_quote: SupportingQuote | None = None

    @model_validator(mode="after")
    def _check_buckets(self) -> "PortfolioSpec":
        if self.long_bucket > self.n_buckets:
            raise ValueError(
                f"long_bucket {self.long_bucket} exceeds n_buckets {self.n_buckets}"
            )
        if self.long_short:
            if self.short_bucket is None:
                raise ValueError("long_short=True requires short_bucket")
            if self.short_bucket == self.long_bucket:
                raise ValueError("short_bucket must differ from long_bucket")
            if self.short_bucket > self.n_buckets or self.short_bucket < 1:
                raise ValueError(
                    f"short_bucket {self.short_bucket} out of range [1, {self.n_buckets}]"
                )
        return self


class RebalanceSpec(BaseModel):
    """When rebalances happen and how execution is timed relative to signals.

    execution_lag_days defaults to 1 (T+1 execution). Papers almost never
    state this explicitly — extracting it accurately is one of the most
    impactful ambiguity-resolution tasks in the pipeline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    frequency: Frequency = "monthly"
    execution_lag_days: int = Field(default=1, ge=0, le=60)
    signal_date_convention: PriceConvention = "close"
    execution_date_convention: ExecutionConvention = "close"
    measure_daily_returns: bool = False
    holding_period_months: int | None = Field(default=None, ge=1)
    supporting_quote: SupportingQuote | None = None

    @model_validator(mode="after")
    def _check_holding_period(self) -> "RebalanceSpec":
        # Only monthly frequency supports holding > 1 period right now.
        # Overlapping-tranche logic in the engine is month-indexed.
        if self.holding_period_months is not None and self.frequency != "monthly":
            raise ValueError(
                "holding_period_months is only supported with frequency='monthly'"
            )
        return self


class ReplicationSpec(BaseModel):
    """Top-level replication contract. Consumed verbatim by the backtest engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paper_id: str
    paper_title: str
    universe: UniverseSpec
    signal: SignalSpec
    portfolio: PortfolioSpec
    rebalance: RebalanceSpec
    start_date: date
    end_date: date
    base_currency: str = "USD"
    ambiguities: tuple[AmbiguityFlag, ...] = ()
    notes: str = ""
    # Optional paper-reported headline number for the variant A1 chose as primary.
    # When present, D2 compares the engine's replication against this claim;
    # when None (purely theoretical paper, no designated headline), D2 is skipped.
    headline_claim: HeadlineClaim | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> "ReplicationSpec":
        if self.end_date <= self.start_date:
            raise ValueError(
                f"end_date {self.end_date} must be after start_date {self.start_date}"
            )
        return self
