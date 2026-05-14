"""Structured schemas that flow between agents and the engine.

Every cross-component payload is a Pydantic v2 model defined here. If a value
isn't represented in a schema, it doesn't exist as far as the pipeline is
concerned — agents cannot ship free-form prose into downstream consumers.
"""

from src.specs.provenance import ProvenanceRecord, SourceTier
from src.specs.claims import PaperClaim, SupportingQuote, ClaimComparison, ClaimVerdict
from src.specs.paper_metrics import (
    HeadlineClaim,
    HeadlineClaimKind,
    MonthlyLongShortReturn,
    NestedConditionalSortReturn,
    SharpeRatioDifference,
    VarianceRatioStatistic,
    RegressionAlpha,
    StatisticalTestClaim,
)
from src.specs.extraction_overrides import (
    PaperExtractionOverride,
    PaperExtractionOverrideKind,
    compute_quote_checksum,
)
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
    FormationDecayPoint,
    BacktestResult,
    AmbiguityDiagnostic,
    ReplicationResult,
    FinalizedReplicationResult,
    OverallConfidence,
    ReturnConvention,
    SpecAdaptation,
    SpecAdaptationKind,
)
from src.specs.factor_comparison import (
    FactorComparison,
    KFFactor,
    ComparisonVerdict,
)
from src.specs.verification import (
    SupportCheck,
    SupportStatus,
    QuoteVerification,
    VerificationReport,
    VerifiedReplicationSpec,
    Criticism,
    CriticismCategory,
    AdversarialCritique,
)
from src.specs.mapping import (
    Fidelity,
    Reasonable,
    FieldMapping,
    DataMapping,
    MappingCheck,
    VerifiedDataMapping,
)
from src.specs.diagnosis import (
    MutationProposal,
    MutationResult,
    MutationDirection,
    DivergenceDiagnosis,
    PrimaryCauseKind,
)
from src.specs.robustness import (
    StressTestFamily,
    SignalType,
    GapAttribution,
    StressTestResult,
    RobustnessScorecard,
    RobustnessJudgment,
)

__all__ = [
    "ProvenanceRecord",
    "SourceTier",
    "PaperClaim",
    "SupportingQuote",
    "ClaimComparison",
    "ClaimVerdict",
    "HeadlineClaim",
    "HeadlineClaimKind",
    "MonthlyLongShortReturn",
    "NestedConditionalSortReturn",
    "SharpeRatioDifference",
    "VarianceRatioStatistic",
    "RegressionAlpha",
    "StatisticalTestClaim",
    "PaperExtractionOverride",
    "PaperExtractionOverrideKind",
    "compute_quote_checksum",
    "AmbiguityFlag",
    "SensitivityPriority",
    "UniverseSpec",
    "SignalSpec",
    "PortfolioSpec",
    "RebalanceSpec",
    "ReplicationSpec",
    "ReturnObservation",
    "SubperiodSummary",
    "FormationDecayPoint",
    "BacktestResult",
    "AmbiguityDiagnostic",
    "ReplicationResult",
    "FinalizedReplicationResult",
    "OverallConfidence",
    "ReturnConvention",
    "SpecAdaptation",
    "SpecAdaptationKind",
    "FactorComparison",
    "KFFactor",
    "ComparisonVerdict",
    "SupportCheck",
    "SupportStatus",
    "QuoteVerification",
    "VerificationReport",
    "VerifiedReplicationSpec",
    "Criticism",
    "CriticismCategory",
    "AdversarialCritique",
    "Fidelity",
    "Reasonable",
    "FieldMapping",
    "DataMapping",
    "MappingCheck",
    "VerifiedDataMapping",
    "MutationProposal",
    "MutationResult",
    "MutationDirection",
    "DivergenceDiagnosis",
    "PrimaryCauseKind",
    "StressTestFamily",
    "SignalType",
    "GapAttribution",
    "StressTestResult",
    "RobustnessScorecard",
    "RobustnessJudgment",
]
