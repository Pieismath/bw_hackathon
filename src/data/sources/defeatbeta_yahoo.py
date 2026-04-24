"""DefeatBeta Yahoo source — local parquet snapshots read via DuckDB.

Why DuckDB: we can query the parquet files directly without loading them
into memory. Columnar pushdown handles the PIT filter efficiently.

Key schema facts to keep in mind (discovered during Phase 1 data audit):
  - `report_date` is VARCHAR in ISO `YYYY-MM-DD` format across all tables.
    Lexical comparison is chronological — we exploit this and skip casting.
  - `stock_statement.report_date` also contains the literal string 'TTM'
    (~1M rows). Every fundamentals query MUST filter `report_date != 'TTM'`.
  - Price values are DECIMAL(38,2); we CAST to DOUBLE on return to avoid
    Decimal objects leaking into pandas downstream.
  - `stock_profile` has NO SIC codes and is a single 2026-04-18 snapshot
    (not a time series). This source does NOT expose sector/industry as
    point-in-time — any caller doing sector filters must flag it.

Survivorship: Yahoo drops delisted tickers. This source contains only
surviving symbols. Universe queries surface this via fidelity_note.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from src.data.sources.base import BaseDataSource, resolve_defeatbeta_snapshot
from src.specs.provenance import SourceTier

log = logging.getLogger(__name__)

REQUIRED_FILES = (
    "stock_prices.parquet",
    "stock_statement.parquet",
    "stock_profile.parquet",
    "stock_shares_outstanding.parquet",
)

_PRICE_COLUMNS = ("symbol", "report_date", "open", "close", "high", "low", "volume")


class DefeatBetaYahooSource(BaseDataSource):
    name = "defeatbeta_yahoo"
    tier: SourceTier = "primary"

    def __init__(self, cache_root: Path | str):
        self.cache_root = Path(cache_root)
        self.snapshot_data_dir = resolve_defeatbeta_snapshot(
            self.cache_root, required_files=list(REQUIRED_FILES)
        )
        self._con = duckdb.connect(database=":memory:")
        # Register each parquet as a zero-copy view so SQL references the
        # file by short name (stock_prices, stock_statement, …).
        for f in REQUIRED_FILES:
            view_name = f.removesuffix(".parquet")
            p = str(self.snapshot_data_dir / f).replace("'", "''")
            # DuckDB DDL does not accept prepared parameters. Paths are
            # internal (resolved from a trusted snapshot dir), so quoting
            # single-quotes is sufficient injection defense.
            self._con.execute(
                f"CREATE OR REPLACE VIEW {view_name} AS "
                f"SELECT * FROM read_parquet('{p}')"
            )

    def close(self) -> None:
        self._con.close()

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def fetch_prices(
        self,
        tickers: list[str],
        as_of_date: date,
        lookback_days: int,
    ) -> pd.DataFrame:
        if not tickers:
            return pd.DataFrame(columns=list(_PRICE_COLUMNS))
        earliest = as_of_date - timedelta(days=lookback_days)
        placeholders = ", ".join(["?"] * len(tickers))
        query = f"""
            SELECT symbol, report_date,
                   CAST(open   AS DOUBLE) AS open,
                   CAST(close  AS DOUBLE) AS close,
                   CAST(high   AS DOUBLE) AS high,
                   CAST(low    AS DOUBLE) AS low,
                   volume
            FROM stock_prices
            WHERE symbol IN ({placeholders})
              AND report_date <= ?
              AND report_date >= ?
            ORDER BY symbol, report_date
        """
        params = [*tickers, as_of_date.isoformat(), earliest.isoformat()]
        return self._con.execute(query, params).df()

    def fetch_latest_close(
        self,
        tickers: list[str],
        as_of_date: date,
    ) -> pd.DataFrame:
        """Latest close on or before `as_of_date` per ticker (one row each)."""
        if not tickers:
            return pd.DataFrame(columns=["symbol", "report_date", "close"])
        placeholders = ", ".join(["?"] * len(tickers))
        query = f"""
            WITH latest AS (
                SELECT symbol, MAX(report_date) AS report_date
                FROM stock_prices
                WHERE symbol IN ({placeholders}) AND report_date <= ?
                GROUP BY symbol
            )
            SELECT sp.symbol, sp.report_date, CAST(sp.close AS DOUBLE) AS close
            FROM stock_prices sp
            JOIN latest l ON sp.symbol = l.symbol AND sp.report_date = l.report_date
        """
        params = [*tickers, as_of_date.isoformat()]
        return self._con.execute(query, params).df()

    def fetch_month_end_prices(
        self,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Last trading day of each month per symbol, within [start, end].

        Returned long-format DataFrame: columns = [symbol, month_end_date, close].
        `month_end_date` is the ISO date of the actual last trading day (e.g.
        2020-06-30), not the calendar month-end. Pivot in the caller if a
        wide panel is needed.
        """
        query = """
            WITH stamped AS (
                SELECT symbol,
                       report_date,
                       CAST(close AS DOUBLE) AS close,
                       strftime(CAST(report_date AS DATE), '%Y-%m') AS ym,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol, strftime(CAST(report_date AS DATE), '%Y-%m')
                           ORDER BY report_date DESC
                       ) AS rn
                FROM stock_prices
                WHERE report_date >= ? AND report_date <= ?
            )
            SELECT symbol, report_date AS month_end_date, close
            FROM stamped
            WHERE rn = 1
            ORDER BY symbol, month_end_date
        """
        return self._con.execute(
            query, [start_date.isoformat(), end_date.isoformat()]
        ).df()

    # ------------------------------------------------------------------
    # Shares outstanding + market caps
    # ------------------------------------------------------------------

    def fetch_shares_outstanding_all(self) -> pd.DataFrame:
        """All shares-outstanding rows across all symbols. Small enough to load in full."""
        query = """
            SELECT symbol, report_date, shares_outstanding
            FROM stock_shares_outstanding
            ORDER BY symbol, report_date
        """
        return self._con.execute(query).df()

    # ------------------------------------------------------------------
    # Universe
    # ------------------------------------------------------------------

    def fetch_recently_active_symbols(
        self,
        as_of_date: date,
        recency_days: int,
    ) -> list[str]:
        """Symbols with any price row in the `recency_days` calendar window
        ending at `as_of_date`. A proxy for 'still trading'."""
        earliest = as_of_date - timedelta(days=recency_days)
        query = """
            SELECT DISTINCT symbol
            FROM stock_prices
            WHERE report_date <= ? AND report_date >= ?
            ORDER BY symbol
        """
        df = self._con.execute(query, [as_of_date.isoformat(), earliest.isoformat()]).df()
        return df["symbol"].tolist()

    def filter_by_min_history(
        self,
        symbols: list[str],
        as_of_date: date,
        min_history_days: int,
    ) -> list[str]:
        """Keep symbols whose earliest PIT price is ≥ min_history_days before as_of_date."""
        if not symbols:
            return []
        cutoff = as_of_date - timedelta(days=min_history_days)
        placeholders = ", ".join(["?"] * len(symbols))
        query = f"""
            SELECT symbol
            FROM (
                SELECT symbol, MIN(report_date) AS first_date
                FROM stock_prices
                WHERE symbol IN ({placeholders}) AND report_date <= ?
                GROUP BY symbol
            ) sub
            WHERE first_date <= ?
            ORDER BY symbol
        """
        params = [*symbols, as_of_date.isoformat(), cutoff.isoformat()]
        return self._con.execute(query, params).df()["symbol"].tolist()

    # ------------------------------------------------------------------
    # Fundamentals
    # ------------------------------------------------------------------

    def fetch_fundamentals_rows(
        self,
        ticker: str,
        as_of_date: date,
        period_type: str,
        filing_lag_days: int,
    ) -> pd.DataFrame:
        """Raw fundamentals rows (long-format) visible under the filing proxy.

        Visibility rule (conservative proxy): a row with period_end
        `report_date` is visible at `as_of_date` iff
        `report_date + filing_lag_days <= as_of_date`. That is, we assume
        the company filed within `filing_lag_days` of fiscal period end.
        If they actually filed later (a late filer), we'd be over-optimistic
        — that requires SEC EDGAR filing dates to fix.

        Also filters out rows where report_date == 'TTM' (a literal string
        in the parquet, not a date).
        """
        cutoff_period_end = as_of_date - timedelta(days=filing_lag_days)
        query = """
            SELECT report_date,
                   finance_type,
                   item_name,
                   CAST(item_value AS DOUBLE) AS item_value
            FROM stock_statement
            WHERE symbol = ?
              AND period_type = ?
              AND report_date != 'TTM'
              AND report_date <= ?
            ORDER BY report_date DESC, finance_type, item_name
        """
        return self._con.execute(
            query, [ticker, period_type, cutoff_period_end.isoformat()]
        ).df()
