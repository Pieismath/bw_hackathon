"""Parse Ken French data-library CSVs into clean dated DataFrames.

The library's CSVs are plain text but quirky:
  - A free-form preamble of variable length (1-15 lines).
  - The actual header row begins with a comma (date column has no name).
  - Returns are stored as percent (2.89 means 2.89%/mo, not 0.0289).
  - Dates are YYYYMM (monthly) or YYYYMMDD (daily) — no separators.
  - Some files have a trailing "Annual Factors:" section after the monthly
    block; we stop parsing at that marker so callers don't accidentally
    mix annual and monthly rows.
  - Footer "Copyright …" line and blank lines must be ignored.

Returns are decimal-scaled (divided by 100) so they match the engine's
arithmetic_monthly convention.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

KF_DIR = Path("data/ken-french")


def _parse_kf_date(s: str) -> date | None:
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    if len(s) == 6 and s.isdigit():
        # Month rows: anchor to month-end is more useful than month-start.
        y, m = int(s[:4]), int(s[4:6])
        # Last day of month.
        if m == 12:
            return date(y, 12, 31)
        from calendar import monthrange
        return date(y, m, monthrange(y, m)[1])
    return None  # year-only or junk; skip


def load_kf_csv(path: Path | str) -> pd.DataFrame:
    """Load one Ken French CSV. Returns DataFrame indexed by date with
    each factor as its own column, values as decimals (not percent).

    Raises FileNotFoundError if `path` is missing.
    """
    path = Path(path)
    text = path.read_text()
    lines = text.splitlines()

    # Find the header line — first line that starts with a comma.
    header_idx = next(
        (i for i, L in enumerate(lines) if L.startswith(",")),
        None,
    )
    if header_idx is None:
        raise ValueError(f"no comma-prefixed header found in {path}")

    columns = [c.strip() for c in lines[header_idx][1:].split(",")]
    rows: list[tuple[date, list[float]]] = []
    for L in lines[header_idx + 1:]:
        s = L.strip()
        if not s:
            continue
        if s.lower().startswith("copyright"):
            break
        if s.lower().startswith("annual factors"):
            # Stop at the annual block so callers don't accidentally mix
            # annual and monthly rows.
            break
        parts = [p.strip() for p in L.split(",")]
        if len(parts) < 2:
            continue
        d = _parse_kf_date(parts[0])
        if d is None:
            # Year-only annual rows ("2025, ...") that slip through if the
            # "Annual Factors" header is absent. Skip them — we only return
            # the monthly/daily series.
            continue
        try:
            vals = [float(p) for p in parts[1:1 + len(columns)]]
        except ValueError:
            continue
        rows.append((d, vals))

    if not rows:
        raise ValueError(f"parsed zero data rows from {path}")

    idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    df = pd.DataFrame([r[1] for r in rows], index=idx, columns=columns)
    # KF reports percent. Engine consumes decimals. Don't normalize columns
    # named explicitly — RF is also percent in the same scale.
    return df / 100.0


def load_ff3_monthly() -> pd.DataFrame:
    """3-factor monthly: Mkt-RF, SMB, HML, RF (decimal, month-end indexed)."""
    return load_kf_csv(KF_DIR / "F-F_Research_Data_Factors.csv")


def load_mom_daily() -> pd.DataFrame:
    """Carhart momentum factor, daily."""
    return load_kf_csv(KF_DIR / "F-F_Momentum_Factor_daily.csv")


def load_ff5_daily() -> pd.DataFrame:
    """5-factor daily: Mkt-RF, SMB, HML, RMW, CMA, RF."""
    return load_kf_csv(KF_DIR / "F-F_Research_Data_5_Factors_2x3_daily.csv")


def daily_to_monthly(daily: pd.DataFrame, column: str) -> pd.Series:
    """Compound daily decimal returns into month-end monthly returns.

    `column` must be present in `daily`. Drops months with any missing values.
    """
    s = daily[column].dropna()
    monthly = (1.0 + s).resample("ME").apply(lambda x: x.prod() - 1.0)
    return monthly
