"""Base interface for data sources + the defeatbeta snapshot glob helper.

Sources are thin adapters that know how to read from one place. They do NOT
apply caching or build provenance chains — the store layer owns both. The
base class exists mostly to pin the name/tier conventions and enforce a
close() method for connection cleanup.
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from pathlib import Path

from src.specs import ProvenanceRecord, SourceTier


class SnapshotSelectionWarning(UserWarning):
    """Emitted when multiple HF snapshot directories are present.

    HF re-downloading a dataset leaves old snapshots in place. When this
    happens we pick the most recent by mtime but the caller should know so
    they can clean up — silently picking the wrong snapshot is a subtle
    source of data drift.
    """


def resolve_defeatbeta_snapshot(
    cache_root: Path,
    required_files: list[str],
) -> Path:
    """Locate the `.../snapshots/<hash>/data` directory for defeatbeta.

    Policy:
      - If no snapshots dir or no snapshot subdir → FileNotFoundError.
      - If multiple snapshots → emit SnapshotSelectionWarning, pick newest by mtime.
      - If the newest snapshot is missing any required file → FileNotFoundError.
    """
    base = cache_root / "defeatbeta" / "datasets--defeatbeta--yahoo-finance-data" / "snapshots"
    if not base.exists():
        raise FileNotFoundError(f"no snapshots dir at {base}")
    snapshots = sorted(
        (p for p in base.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not snapshots:
        raise FileNotFoundError(f"no snapshots present in {base}")
    if len(snapshots) > 1:
        warnings.warn(
            f"multiple defeatbeta snapshots found under {base}: "
            f"{[s.name for s in snapshots]}. Using newest by mtime: {snapshots[0].name}. "
            "Delete the older snapshots to make this deterministic.",
            SnapshotSelectionWarning,
            stacklevel=2,
        )
    data_dir = snapshots[0] / "data"
    missing = [f for f in required_files if not (data_dir / f).exists()]
    if missing:
        raise FileNotFoundError(
            f"required files missing from snapshot {snapshots[0].name}: {missing}"
        )
    return data_dir


class BaseDataSource(ABC):
    """Minimal contract every data source must honor."""

    name: str
    tier: SourceTier

    @abstractmethod
    def close(self) -> None:
        """Release any long-lived resources (DB connections, HTTP sessions)."""

    def build_provenance(
        self,
        as_of_date=None,
        notes: str = "",
        fidelity_note: str | None = None,
        substitution_flag: bool = False,
    ) -> ProvenanceRecord:
        return ProvenanceRecord(
            source_id=self.name,
            source_tier=self.tier,
            as_of_date=as_of_date,
            notes=notes,
            fidelity_note=fidelity_note,
            substitution_flag=substitution_flag,
        )
