"""Data source catalog.

A literal description of what's available in our `PointInTimeDataStore`,
used as context for B1 (Data Mapper). Kept as a single source of truth
so B1's prompt always sees the current catalog. B2's deterministic
checks consult the same structure.

When adding a new source, update `CATALOG` here and B2's deterministic
checks will pick it up automatically.
"""

from __future__ import annotations

from typing import TypedDict


class SourceMethod(TypedDict):
    name: str
    signature: str
    returns: str
    notes: str


class SourceEntry(TypedDict):
    name: str
    tier: str
    description: str
    coverage: str
    caveats: list[str]
    methods: list[SourceMethod]


CATALOG: list[SourceEntry] = [
    {
        "name": "defeatbeta_yahoo",
        "tier": "primary",
        "description": (
            "Local parquet snapshot of defeatbeta's yahoo-finance-data. "
            "Queried via DuckDB through PointInTimeDataStore."
        ),
        "coverage": (
            "US equities; daily OHLCV from 1994-11-30 to 2026-04-17 "
            "(~11k symbols). Quarterly shares outstanding. Fundamentals "
            "(income/balance/cash-flow) only from 2019-05 onward."
        ),
        "caveats": [
            "SURVIVORSHIP BIAS: Yahoo drops delisted tickers. Universe "
            "queries surface this via provenance.fidelity_note.",
            "Prices are split- and spin-off-adjusted backwards (total-"
            "return-equivalent). Cross-sectional rankings are preserved.",
            "No SIC codes; only a stale-2026 sector string on stock_profile.",
            "No exchange flags — can't distinguish NYSE from AMEX / NASDAQ "
            "at the symbol level without external data.",
        ],
        "methods": [
            {
                "name": "get_prices",
                "signature": "(tickers: list[str], as_of_date: date, lookback_days: int) -> QueryResult[DataFrame]",
                "returns": "long-format daily OHLCV with symbol, report_date, open, close, high, low, volume",
                "notes": "PIT: only rows with report_date <= as_of_date.",
            },
            {
                "name": "get_monthly_close_panel",
                "signature": "(start_date: date, end_date: date) -> QueryResult[wide DataFrame]",
                "returns": "wide panel [month_end_date, symbol] = close; used by the backtest engine",
                "notes": "month-end normalized; all symbols with any month-end price.",
            },
            {
                "name": "get_market_cap_panel",
                "signature": "(start_date: date, end_date: date) -> QueryResult[wide DataFrame]",
                "returns": "wide panel [month_end_date, symbol] = close * asof-shares; caps > $10T filtered as data artifacts",
                "notes": "for VW weighting.",
            },
            {
                "name": "get_universe",
                "signature": "(name: UniverseName, as_of_date: date, min_price: float|None, min_history_days: int|None, recency_days: int) -> QueryResult[list[str]]",
                "returns": "list of active symbols at as_of_date",
                "notes": "only universe currently supported: 'defeatbeta_all_equities'.",
            },
            {
                "name": "get_fundamentals",
                "signature": "(ticker: str, as_of_date: date, period_type: 'annual'|'quarterly', filing_lag_days: int|None) -> FundamentalsSnapshot|None",
                "returns": "latest fundamentals row whose period_end + filing_lag_days <= as_of_date",
                "notes": "coverage begins 2019-05; ONLY usable for post-2019 papers.",
            },
        ],
    },
    {
        "name": "ken_french_csv",
        "tier": "supplementary",
        "description": (
            "Ken French Data Library MOM factor. Monthly time series only, "
            "1927-present. Loaded via src.data.ken_french.load_kf_mom_monthly."
        ),
        "coverage": "1927-01 to 2026-02, monthly, US.",
        "caveats": [
            "Not a per-stock source — provides factor returns only.",
            "Useful for EXTERNAL VALIDATION of a replicated factor series; "
            "not substitutable for per-stock price/return data.",
        ],
        "methods": [
            {
                "name": "load_kf_mom_monthly",
                "signature": "(csv_path: Path) -> pd.Series",
                "returns": "monthly MOM factor series indexed by month-end timestamp",
                "notes": "decimal fractions (not percent); used for correlation checks.",
            },
        ],
    },
]


def catalog_as_prompt_text() -> str:
    """Render the catalog as human-readable text for agent prompts."""
    lines: list[str] = []
    for src in CATALOG:
        lines.append(f"## {src['name']} (tier: {src['tier']})")
        lines.append(f"{src['description']}")
        lines.append(f"Coverage: {src['coverage']}")
        lines.append("Caveats:")
        for c in src["caveats"]:
            lines.append(f"  - {c}")
        lines.append("Methods:")
        for m in src["methods"]:
            lines.append(f"  - {m['name']}{m['signature']}")
            lines.append(f"      returns: {m['returns']}")
            if m["notes"]:
                lines.append(f"      notes: {m['notes']}")
        lines.append("")
    return "\n".join(lines)


def source_exists(name: str) -> bool:
    return any(s["name"] == name for s in CATALOG)


def method_exists(source: str, method: str) -> bool:
    for s in CATALOG:
        if s["name"] == source:
            return any(m["name"] == method for m in s["methods"])
    return False
