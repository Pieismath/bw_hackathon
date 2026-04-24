"""D2 Divergence Diagnostician schemas.

D2 answers the question: "given a gap between the paper's claim and our
engine's replication, WHICH methodological choice is driving the gap?"

The answer is grounded in *actual engine reruns*, not LLM reasoning about
what might happen. Each `MutationResult` is a before/after gap measurement
after mutating exactly one spec field and rerunning the canonical engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.specs.claims import ClaimVerdict


MutationDirection = Literal["close", "widen", "unknown"]

PrimaryCauseKind = Literal[
    "single_field",   # one spec field fully (or near-fully) closes the gap
    "coupled",        # two+ fields must change together; no single mutation suffices
    "data_window",    # sample-era / coverage mismatch drives the residual
    "other",          # anything else (includes "no cause found" cases)
]


class MutationProposal(BaseModel):
    """One proposal from the LLM: mutate one spec field and rerun the engine.

    `parameter` uses dotted-path notation (e.g. `"portfolio.long_bucket"`,
    `"signal.skip_months"`). Depth up to 2 is supported by the code that
    applies the mutation; deeper paths will raise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    parameter: str = Field(min_length=1, max_length=100)
    to_value: str = Field(
        min_length=1, max_length=100,
        description="New value as a string. Pydantic coerces to the target "
                    "field's type during re-validation (e.g. '1' -> 1 for int).",
    )
    rationale: str = Field(min_length=1, max_length=800)
    expected_direction: MutationDirection


class MutationResult(BaseModel):
    """Record of one experiment: proposal + engine rerun + gap delta."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal: MutationProposal
    from_value_human: str          # human-readable representation of the prior value
    pre_abs_gap: float = Field(ge=0.0)    # |gap| before this single-variable mutation
    post_abs_gap: float = Field(ge=0.0)   # |gap| after this mutation
    gap_delta: float               # pre - post; positive = gap closed
    pre_mean_return: float
    post_mean_return: float
    pre_tstat: float | None
    post_tstat: float | None
    verdict_before: ClaimVerdict
    verdict_after: ClaimVerdict
    closed_sign_flip: bool         # went from opposite_sign to same-sign
    notes: str = ""


class DivergenceDiagnosis(BaseModel):
    """D2's final output — a structured, engine-grounded diagnosis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary_cause: str = Field(min_length=1, max_length=200)
    primary_cause_kind: PrimaryCauseKind
    primary_cause_summary: str = Field(
        min_length=1,
        max_length=300,
        description="One sentence human-readable description of the primary cause.",
    )
    primary_cause_evidence: str = Field(min_length=1, max_length=800)
    experiments_run: int = Field(ge=0)
    mutation_results: tuple[MutationResult, ...]
    alternatives_tested: tuple[str, ...]     # dotted paths of every mutation that ran
    alternatives_ruled_out: tuple[str, ...]  # paths whose mutations did NOT close gap
    residual_abs_gap: float = Field(ge=0.0)
    # Bumped to 1200 after observing Opus needing ~600-800 chars to name
    # multiple residual drivers honestly (sample window + survivorship +
    # skip-month + construction). Tighter limits forced hedging.
    residual_gap_likely_cause: str = Field(min_length=1, max_length=1200)
    confidence: Literal["high", "medium", "low"]
    early_exit: bool
    early_exit_reason: str | None = None
