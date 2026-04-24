"""Query-level disk cache.

Keyed on (source, method, args). `args` is a JSON-serializable dict — the
store is responsible for passing stable representations (sorted ticker lists,
ISO-format dates). Never invalidated: point-in-time data is immutable by
definition. If you need to refresh, delete the cache dir.

Values are pickled. Parquet would be nicer for inspection but mixing formats
complicates the API — pickle round-trips any Python object including
Pydantic models, pandas DataFrames, and None.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

_MISS = object()
"""Sentinel for cache misses — distinguishes 'not cached' from 'cached None'."""


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"cannot serialize {type(obj).__name__} for cache key")


def make_cache_key(source: str, method: str, args: dict[str, Any]) -> str:
    """Build a stable, filesystem-safe cache key.

    Stable across dict key order. Dates and sets are normalized to canonical
    string form. The returned key includes readable source/method prefixes
    so cache files are greppable on disk.
    """
    payload = {"source": source, "method": method, "args": args}
    serialized = json.dumps(payload, default=_json_default, sort_keys=True)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"{source}__{method}__{digest}"


class QueryCache:
    """Disk-backed, never-invalidated cache for point-in-time query results."""

    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.pkl"

    def _get_raw(self, key: str) -> Any:
        p = self._path(key)
        if not p.exists():
            return _MISS
        with p.open("rb") as f:
            return pickle.load(f)

    def put(self, key: str, value: Any) -> None:
        p = self._path(key)
        tmp = p.with_suffix(".pkl.tmp")
        with tmp.open("wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(p)

    def get_or_compute(self, key: str, compute: Callable[[], T]) -> T:
        """Return the cached value for `key`, or compute, store, and return it."""
        hit = self._get_raw(key)
        if hit is _MISS:
            value = compute()
            self.put(key, value)
            return value
        return hit
