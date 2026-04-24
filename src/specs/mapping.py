"""B1/B2 data-mapping schemas.

A1 says *what* the paper's methodology is (`ReplicationSpec`).
B1 says *how to get the data* needed to run that spec on our local sources
(`DataMapping`). B2 verifies B1's choices are sound (`VerifiedDataMapping`).
The engine uses both: it consumes the spec and the mapping's method
handles identify the data-source calls to route through.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Fidelity = Literal["high", "medium", "low"]
Reasonable = Literal["yes", "no", "partial"]


class FieldMapping(BaseModel):
    """One line of the data plan: which source method feeds which spec field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_field: str
    source_name: str
    method: str
    fidelity: Fidelity
    fidelity_notes: str = Field(min_length=1, max_length=500)


class DataMapping(BaseModel):
    """B1's output — the data plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mappings: tuple[FieldMapping, ...]
    notes: str = ""


class MappingCheck(BaseModel):
    """One B2 verification record per FieldMapping.

    Deterministic checks + LLM reasonableness judgment. `blocking` marks a
    mapping that must be fixed before the engine can run. Non-blocking
    issues degrade the overall fidelity but do not halt the pipeline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_mapping: FieldMapping
    source_exists: bool
    date_range_ok: bool
    fields_present: bool
    llm_reasonable: Reasonable
    llm_reason: str
    blocking: bool
    notes: str = ""


class VerifiedDataMapping(BaseModel):
    """B2's output — the mapping plus per-mapping checks and global status."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping: DataMapping
    checks: tuple[MappingCheck, ...]
    blocking_issues: tuple[str, ...]
    overall_fidelity: Fidelity
