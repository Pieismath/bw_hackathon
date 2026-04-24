"""Ken French Data Library CSV loader — minimal subset we need now.

The full `KenFrenchSource` (Phase 3) will handle the various factor files
and vintage rolling. For Phase 1 validation all we need is the monthly
MOM factor, parsed from the CSV Ken French publishes on his Tuck page.

File format (after a header blurb):
    ,Mom
    192701,   0.57
    192702,  -1.50
    ...
    202602,   1.33
    <blank>
     Annual Factors:
    ...

Values are in percent per month. We return decimal fractions.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_kf_mom_monthly(csv_path: Path | str) -> pd.Series:
    """Parse the monthly section of Ken French's MOM factor into a Series.

    Returns a pd.Series indexed by a month-end DatetimeIndex (timestamps
    aligned to the last calendar day of the month, e.g. 2020-12-31), with
    values in decimal fractions (not percent).
    """
    path = Path(csv_path)
    lines = path.read_text().splitlines()

    header_idx = next(
        (i for i, ln in enumerate(lines) if ln.strip().startswith(",Mom")),
        None,
    )
    if header_idx is None:
        raise ValueError(f"could not find ',Mom' header in {path}")

    rows: list[tuple[str, float]] = []
    for ln in lines[header_idx + 1 :]:
        if not ln.strip():
            break  # blank line separates monthly from annual section
        if "Annual" in ln:
            break
        parts = ln.split(",")
        if len(parts) != 2:
            continue
        ym = parts[0].strip()
        val = parts[1].strip()
        if len(ym) != 6 or not ym.isdigit():
            # Annual rows (YYYY) are 4 chars — skip if we somehow get there.
            continue
        try:
            rows.append((ym, float(val)))
        except ValueError:
            continue

    if not rows:
        raise ValueError(f"no monthly MOM rows parsed from {path}")

    df = pd.DataFrame(rows, columns=["yyyymm", "mom_pct"])
    df["month_end"] = (
        pd.to_datetime(df["yyyymm"], format="%Y%m").dt.to_period("M").dt.to_timestamp("M").dt.normalize()
    )
    series = df.set_index("month_end")["mom_pct"].astype(float) / 100.0
    series.name = "kf_mom"
    return series.sort_index()
