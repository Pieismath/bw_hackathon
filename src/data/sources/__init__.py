"""Data source adapters. Each knows how to read from one backing system."""

from src.data.sources.base import (
    BaseDataSource,
    SnapshotSelectionWarning,
    resolve_defeatbeta_snapshot,
)
from src.data.sources.defeatbeta_yahoo import DefeatBetaYahooSource

__all__ = [
    "BaseDataSource",
    "DefeatBetaYahooSource",
    "SnapshotSelectionWarning",
    "resolve_defeatbeta_snapshot",
]
