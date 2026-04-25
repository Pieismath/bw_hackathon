"""Step 11.1 — Subperiod stability.

Slice the spec's date range into halves, thirds, and a small set of named
regimes (dotcom unwind, GFC, COVID, momentum crash). For each window that
intersects the spec's range, rerun the engine with the dates clipped and
record the headline metric.

Mentor framing: "where in time does the alpha live?" If the strategy
survives the second half but not the first (or vice versa), that's a
post-publication-decay signal D3 should pick up.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.agents.validation import run_backtest_cached
from src.data.store import PointInTimeDataStore
from src.specs import ReplicationSpec, StressTestResult

SURVIVING_TSTAT = 1.5

# Named regimes — clipped to spec range at run-time. Dates inclusive.
NAMED_REGIMES: list[tuple[str, date, date]] = [
    ("dotcom_unwind",     date(2000, 1, 1),  date(2002, 12, 31)),
    ("gfc_2008_2009",     date(2008, 1, 1),  date(2009, 12, 31)),
    ("momentum_crash_2009", date(2009, 3, 1),  date(2009, 8, 31)),
    ("covid_2020",        date(2020, 1, 1),  date(2020, 12, 31)),
    ("post_2010",         date(2010, 1, 1),  date(2024, 12, 31)),
    ("pre_2010",          date(1995, 1, 1),  date(2009, 12, 31)),
]


def _midpoint(a: date, b: date) -> date:
    return a + (b - a) // 2


def _windows_for(spec: ReplicationSpec) -> list[tuple[str, date, date]]:
    """Generate (name, start, end) tuples that intersect spec's range."""
    s, e = spec.start_date, spec.end_date
    if e <= s:
        return []
    mid = _midpoint(s, e)
    third1 = s + (e - s) // 3
    third2 = s + 2 * (e - s) // 3
    structural = [
        ("first_half", s, mid),
        ("second_half", mid + timedelta(days=1), e),
        ("first_third", s, third1),
        ("middle_third", third1 + timedelta(days=1), third2),
        ("last_third", third2 + timedelta(days=1), e),
    ]
    regimes: list[tuple[str, date, date]] = []
    for name, rs, re_ in NAMED_REGIMES:
        clip_s = max(rs, s)
        clip_e = min(re_, e)
        if clip_e <= clip_s:
            continue
        # Demand at least 6 months of data in the window for statistics
        if (clip_e - clip_s).days < 180:
            continue
        regimes.append((name, clip_s, clip_e))
    return structural + regimes


def run_subperiod_battery(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    transaction_cost_bps: float = 0.0,
) -> list[StressTestResult]:
    results: list[StressTestResult] = []
    for name, ws, we in _windows_for(spec):
        mutated = spec.model_copy(update={"start_date": ws, "end_date": we})
        try:
            bt = run_backtest_cached(
                mutated, store, transaction_cost_bps=transaction_cost_bps
            )
        except RuntimeError as e:
            results.append(
                StressTestResult(
                    name=f"subperiod_{name}",
                    family="subperiod",
                    parameter_swept={"start_date": ws.isoformat(), "end_date": we.isoformat()},
                    headline_metric=0.0,
                    headline_tstat=None,
                    n_periods=0,
                    surviving=False,
                    notes=f"engine error on subperiod: {e}",
                )
            )
            continue
        results.append(
            StressTestResult(
                name=f"subperiod_{name}",
                family="subperiod",
                parameter_swept={"start_date": ws.isoformat(), "end_date": we.isoformat()},
                headline_metric=bt.mean_return,
                headline_tstat=bt.alpha_tstat,
                n_periods=bt.n_periods,
                surviving=(bt.mean_return > 0 and bt.alpha_tstat > SURVIVING_TSTAT),
                notes=f"clipped sample {ws} to {we}",
                spec_hash=bt.spec_hash,
            )
        )
    return results
