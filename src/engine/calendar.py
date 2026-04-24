"""Calendar helpers for the backtest engine.

All dates in the engine are canonical calendar month-ends (last day of
month). Price panels are normalized to this convention in the data store,
so the engine never has to worry about whether a symbol's last-trading-day
of the month was the 29th, 30th, or 31st.

Execution lag is measured in business days, applied to the formation date.
`business_day_shift(d, n)` returns d + n business days via pandas' BDay.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def month_ends_in(start_date: date, end_date: date) -> list[pd.Timestamp]:
    """Calendar month-end timestamps that fall within [start_date, end_date].

    Note the inclusive-both-ends semantics operate on the month-end dates
    themselves — we do NOT round start_date up or end_date down to a month
    boundary. If start_date is mid-month the first month-end we return is
    the same month; if end_date is mid-month we stop at the previous month's end.
    """
    if end_date < start_date:
        return []
    idx = pd.date_range(
        start=pd.Timestamp(start_date).normalize(),
        end=pd.Timestamp(end_date).normalize(),
        freq="ME",
    )
    return list(idx.normalize())


def business_day_shift(d: pd.Timestamp | date, n_business_days: int) -> pd.Timestamp:
    """d + n_business_days (Mon–Fri, no holiday calendar). Forward when n > 0."""
    ts = pd.Timestamp(d)
    if n_business_days == 0:
        return ts.normalize()
    return (ts + pd.tseries.offsets.BDay(n_business_days)).normalize()
