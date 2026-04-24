"""Point-in-time data layer.

Structural guarantee: every data access requires an explicit `as_of_date`.
There is no "latest" convenience anywhere in this package — that's what
keeps the rest of the system honest about look-ahead bias.
"""

from src.data.cache import QueryCache, make_cache_key
from src.data.sources.base import BaseDataSource, resolve_defeatbeta_snapshot
from src.data.sources.defeatbeta_yahoo import DefeatBetaYahooSource
from src.data.store import (
    FundamentalsSnapshot,
    PointInTimeDataStore,
    QueryResult,
)

__all__ = [
    "BaseDataSource",
    "DefeatBetaYahooSource",
    "FundamentalsSnapshot",
    "PointInTimeDataStore",
    "QueryCache",
    "QueryResult",
    "make_cache_key",
    "resolve_defeatbeta_snapshot",
]
