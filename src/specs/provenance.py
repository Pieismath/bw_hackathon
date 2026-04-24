"""Provenance records attach to every fact, number, and claim in the pipeline.

Every datapoint that surfaces in the final report must trace back through a
chain of ProvenanceRecord entries to (a) the agent or code that produced it,
(b) the data source that fed it, and (c) the paper quote that justified it.
The web UI uses record_id + parent_ids to render the drill-down lineage.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

SourceTier = Literal[
    "primary",       # canonical source used directly (e.g. defeatbeta prices)
    "supplementary", # pulled on demand, still authoritative (e.g. ALFRED, EDGAR)
    "substituted",   # stand-in when requested source unavailable — must flag
    "synthesized",   # produced by an agent or code, not fetched
]


class ProvenanceRecord(BaseModel):
    """One link in the provenance chain.

    A record can describe a data fetch (source_id = "defeatbeta_yahoo"), an
    agent output (source_id = "claude-opus-4-7:methodology_extractor"), or
    a derived computation (source_id = "engine:backtest"). Chain records via
    parent_ids to preserve full lineage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    source_tier: SourceTier
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    as_of_date: date | None = None
    parent_ids: tuple[str, ...] = ()
    substitution_flag: bool = False
    fidelity_note: str | None = None
    notes: str = ""

    def child(
        self,
        source_id: str,
        source_tier: SourceTier,
        **overrides,
    ) -> "ProvenanceRecord":
        """Build a downstream record that lists this record as a parent."""
        return ProvenanceRecord(
            source_id=source_id,
            source_tier=source_tier,
            parent_ids=(*self.parent_ids, self.record_id),
            **overrides,
        )
