"""Structured schemas that flow between agents and the engine.

Every cross-component payload is a Pydantic v2 model defined here. If a value
isn't represented in a schema, it doesn't exist as far as the pipeline is
concerned — agents cannot ship free-form prose into downstream consumers.
"""

from src.specs.provenance import ProvenanceRecord, SourceTier
from src.specs.claims import PaperClaim, SupportingQuote, ClaimComparison, ClaimVerdict
from src.specs.methodology import (
    AmbiguityFlag,
    SensitivityPriority,
    UniverseSpec,
    SignalSpec,
    PortfolioSpec,
    RebalanceSpec,
    ReplicationSpec,
)
from src.specs.results import (
    ReturnObservation,
    SubperiodSummary,
    BacktestResult,
    AmbiguityDiagnostic,
    ReplicationResult,
    FinalizedReplicationResult,
    OverallConfidence,
    ReturnConvention,
)
from src.specs.verification import (
    SupportCheck,
    SupportStatus,
    QuoteVerification,
    VerificationReport,
    VerifiedReplicationSpec,
)

__all__ = [
    "ProvenanceRecord",
    "SourceTier",
    "PaperClaim",
    "SupportingQuote",
    "ClaimComparison",
    "ClaimVerdict",
    "AmbiguityFlag",
    "SensitivityPriority",
    "UniverseSpec",
    "SignalSpec",
    "PortfolioSpec",
    "RebalanceSpec",
    "ReplicationSpec",
    "ReturnObservation",
    "SubperiodSummary",
    "BacktestResult",
    "AmbiguityDiagnostic",
    "ReplicationResult",
    "FinalizedReplicationResult",
    "OverallConfidence",
    "ReturnConvention",
    "SupportCheck",
    "SupportStatus",
    "QuoteVerification",
    "VerificationReport",
    "VerifiedReplicationSpec",
]
