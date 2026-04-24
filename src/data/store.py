"""PointInTimeDataStore — THE structural guarantor of PIT correctness.

Every method requires `as_of_date`. The store never exposes a `latest`
convenience — callers who want "now" must pass `date.today()` explicitly,
which makes the choice visible in provenance and in code review.

Design shape:
  - sources (e.g. DefeatBetaYahooSource) are thin adapters that know how to
    pull rows. They do NOT cache and do NOT produce ProvenanceRecords.
  - the store wraps source calls in `QueryCache.get_or_compute` (keyed on
    source/method/args) and in a ProvenanceRecord.
  - return values are `QueryResult[T]` or, for fundamentals,
    `FundamentalsSnapshot | None`. Both carry provenance.

The fundamentals filing-lag proxy is configurable at construction and
overridable per-query. The default is 90 days — reasonable for US 10-Q
filings, but papers like Novy-Marx use 180 days to capture late 10-K filers.
Callers must record the choice as an AmbiguityFlag on the ReplicationSpec.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Generic, Literal, TypeVar

import pandas as pd
from pydantic import BaseModel, ConfigDict

from src.data.cache import QueryCache, make_cache_key
from src.data.sources.base import BaseDataSource
from src.data.sources.defeatbeta_yahoo import DefeatBetaYahooSource
from src.specs import ProvenanceRecord

T = TypeVar("T")

UniverseName = Literal["defeatbeta_all_equities"]
PeriodType = Literal["annual", "quarterly"]


@dataclass(frozen=True)
class QueryResult(Generic[T]):
    """A point-in-time query result with its provenance.

    `data` is the raw payload (DataFrame, list, etc). `provenance` traces
    where it came from so the final report can link to lineage.
    """

    data: T
    as_of_date: date
    provenance: ProvenanceRecord


class FundamentalsSnapshot(BaseModel):
    """Point-in-time fundamentals for a single ticker.

    `latest_period_end` is the most-recent fiscal period whose filing proxy
    date was ≤ `as_of_date`. `filing_lag_days` records the proxy actually
    applied (may differ from the store default if overridden per-query).
    `items` is a dict of line-item name → value, flattened across finance
    types (balance_sheet / income_statement / cash_flow) because they all
    share the same period_end at this granularity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    as_of_date: date
    filing_lag_days: int
    latest_period_end: date
    period_type: PeriodType
    items: dict[str, float]
    provenance: ProvenanceRecord


class PointInTimeDataStore:
    def __init__(
        self,
        sources: dict[str, BaseDataSource],
        cache_dir: Path | str,
        fundamentals_filing_lag_days: int = 90,
    ):
        self.sources = sources
        self.cache = QueryCache(cache_dir)
        self.fundamentals_filing_lag_days = fundamentals_filing_lag_days

    def _defeatbeta(self) -> DefeatBetaYahooSource:
        src = self.sources.get("defeatbeta_yahoo")
        if src is None:
            raise RuntimeError(
                "defeatbeta_yahoo source is not configured on this store"
            )
        assert isinstance(src, DefeatBetaYahooSource)
        return src

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def get_prices(
        self,
        tickers: list[str],
        as_of_date: date,
        lookback_days: int,
    ) -> QueryResult[pd.DataFrame]:
        tickers_sorted = sorted(set(tickers))
        src = self._defeatbeta()
        key = make_cache_key(
            "defeatbeta_yahoo",
            "get_prices",
            args={
                "tickers": tickers_sorted,
                "as_of_date": as_of_date,
                "lookback_days": lookback_days,
            },
        )
        df = self.cache.get_or_compute(
            key,
            lambda: src.fetch_prices(tickers_sorted, as_of_date, lookback_days),
        )
        provenance = src.build_provenance(
            as_of_date=as_of_date,
            notes=f"get_prices(n={len(tickers_sorted)}, lookback={lookback_days}d)",
        )
        return QueryResult(data=df, as_of_date=as_of_date, provenance=provenance)

    # ------------------------------------------------------------------
    # Universe
    # ------------------------------------------------------------------

    def get_universe(
        self,
        universe_name: UniverseName,
        as_of_date: date,
        min_price: float | None = None,
        min_history_days: int | None = None,
        recency_days: int = 14,
    ) -> QueryResult[list[str]]:
        if universe_name != "defeatbeta_all_equities":
            raise ValueError(f"unknown universe {universe_name!r}")
        src = self._defeatbeta()
        key = make_cache_key(
            "defeatbeta_yahoo",
            "get_universe",
            args={
                "universe_name": universe_name,
                "as_of_date": as_of_date,
                "min_price": min_price,
                "min_history_days": min_history_days,
                "recency_days": recency_days,
            },
        )

        def compute() -> list[str]:
            symbols = src.fetch_recently_active_symbols(as_of_date, recency_days)
            if min_price is not None and symbols:
                latest = src.fetch_latest_close(symbols, as_of_date)
                kept = latest.loc[latest["close"] >= min_price, "symbol"]
                symbols = kept.tolist()
            if min_history_days is not None and symbols:
                symbols = src.filter_by_min_history(
                    symbols, as_of_date, min_history_days
                )
            return sorted(symbols)

        symbols = self.cache.get_or_compute(key, compute)
        provenance = src.build_provenance(
            as_of_date=as_of_date,
            notes=(
                f"universe={universe_name} n={len(symbols)} "
                f"min_price={min_price} min_history_days={min_history_days}"
            ),
            fidelity_note=(
                "Yahoo source excludes delisted tickers — universe has "
                "survivorship bias. Any backtest built on this universe "
                "overstates alpha; surface this in the robustness scorecard."
            ),
        )
        return QueryResult(
            data=symbols, as_of_date=as_of_date, provenance=provenance
        )

    # ------------------------------------------------------------------
    # Bulk panels (engine-side loading)
    # ------------------------------------------------------------------

    def get_monthly_close_panel(
        self,
        start_date: date,
        end_date: date,
    ) -> QueryResult[pd.DataFrame]:
        """Wide month-end close panel. Rows = month_end_date, cols = symbol.

        NOT filtered to a universe — PIT filtering is a per-month decision
        the engine makes using the store's get_universe. This panel is the
        shared backing store for all downstream monthly computations.
        """
        if end_date <= start_date:
            raise ValueError(f"end_date {end_date} must be after start_date {start_date}")
        src = self._defeatbeta()
        key = make_cache_key(
            "defeatbeta_yahoo",
            "get_monthly_close_panel",
            args={"start_date": start_date, "end_date": end_date},
        )

        def compute() -> pd.DataFrame:
            long = src.fetch_month_end_prices(start_date, end_date)
            if long.empty:
                return pd.DataFrame()
            # Normalize varying per-symbol last-trading-days within a month
            # to a canonical calendar month-end timestamp (e.g. 2020-12-31).
            # Some thin-volume stocks' last trade of the month lands on
            # Dec 29 while others land on Dec 31 — we collapse both to the
            # same index bucket so the wide pivot has one row per month.
            long["month_end_date"] = (
                pd.to_datetime(long["month_end_date"])
                .dt.to_period("M").dt.to_timestamp("M").dt.normalize()
            )
            long = long.drop_duplicates(subset=["symbol", "month_end_date"], keep="last")
            wide = long.pivot(index="month_end_date", columns="symbol", values="close")
            return wide.sort_index()

        wide = self.cache.get_or_compute(key, compute)
        provenance = src.build_provenance(
            as_of_date=end_date,
            notes=f"monthly close panel [{start_date}, {end_date}]",
        )
        return QueryResult(data=wide, as_of_date=end_date, provenance=provenance)

    def get_market_cap_panel(
        self,
        start_date: date,
        end_date: date,
    ) -> QueryResult[pd.DataFrame]:
        """Wide month-end market cap panel via asof-join of shares onto prices.

        market_cap(symbol, month_end) = close(symbol, month_end) × latest
        shares_outstanding(symbol) reported on or before month_end. Asof join
        handled in pandas since shares data is quarterly and small.
        """
        if end_date <= start_date:
            raise ValueError(f"end_date {end_date} must be after start_date {start_date}")
        src = self._defeatbeta()
        key = make_cache_key(
            "defeatbeta_yahoo",
            "get_market_cap_panel",
            args={"start_date": start_date, "end_date": end_date},
        )

        def compute() -> pd.DataFrame:
            prices_long = src.fetch_month_end_prices(start_date, end_date)
            shares = src.fetch_shares_outstanding_all()
            if prices_long.empty or shares.empty:
                return pd.DataFrame()
            prices_long["month_end_date"] = (
                pd.to_datetime(prices_long["month_end_date"])
                .dt.to_period("M").dt.to_timestamp("M").dt.normalize()
            )
            prices_long = prices_long.drop_duplicates(
                subset=["symbol", "month_end_date"], keep="last"
            )
            shares["report_date"] = pd.to_datetime(shares["report_date"])
            # merge_asof needs BOTH sides sorted globally on the key column,
            # not just within-group. Sort the key-column first, then by the
            # `by` column as tiebreaker, to satisfy pandas 3's strict check.
            prices_long = prices_long.sort_values(["month_end_date", "symbol"]).reset_index(drop=True)
            shares = shares.sort_values(["report_date", "symbol"]).reset_index(drop=True)
            merged = pd.merge_asof(
                prices_long,
                shares.rename(columns={"report_date": "sos_date"}),
                left_on="month_end_date",
                right_on="sos_date",
                by="symbol",
                direction="backward",
            )
            merged["market_cap"] = merged["close"] * merged["shares_outstanding"]
            # Yahoo's back-adjusted prices include data artifacts for thinly
            # traded tickers — e.g. a ticker with "close" = $500,000,000 and
            # 27M shares would register a $13.5e15 market cap. As of the
            # reference date (2026) no legitimate US equity has exceeded
            # ~$4T; we cap at $10T as a conservative data-quality filter.
            # Legitimate mega-caps (AAPL, MSFT, NVDA, etc.) are unaffected.
            #
            # TODO (phase 4): replace $10T market-cap cap with dollar-volume-
            # based liquidity screen. Current cap handles obvious data
            # artifacts but some $100B-$1T ghost caps survive and dominate
            # VW baskets (see Phase 1 KF-convention VW correlation gap of
            # 0.19 vs EW 0.80).
            artifact_cap_usd = 10_000_000_000_000.0  # $10 trillion
            merged.loc[merged["market_cap"] > artifact_cap_usd, "market_cap"] = pd.NA
            wide = merged.pivot(
                index="month_end_date", columns="symbol", values="market_cap"
            )
            return wide.sort_index()

        wide = self.cache.get_or_compute(key, compute)
        provenance = src.build_provenance(
            as_of_date=end_date,
            notes=f"market cap panel [{start_date}, {end_date}]",
            fidelity_note=(
                "Market cap uses asof-joined quarterly shares_outstanding. "
                "Intra-quarter share changes (buybacks, issuances) are not reflected."
            ),
        )
        return QueryResult(data=wide, as_of_date=end_date, provenance=provenance)

    # ------------------------------------------------------------------
    # Fundamentals
    # ------------------------------------------------------------------

    def get_fundamentals(
        self,
        ticker: str,
        as_of_date: date,
        period_type: PeriodType = "annual",
        filing_lag_days: int | None = None,
    ) -> FundamentalsSnapshot | None:
        lag = (
            filing_lag_days
            if filing_lag_days is not None
            else self.fundamentals_filing_lag_days
        )
        src = self._defeatbeta()
        key = make_cache_key(
            "defeatbeta_yahoo",
            "get_fundamentals",
            args={
                "ticker": ticker,
                "as_of_date": as_of_date,
                "period_type": period_type,
                "filing_lag_days": lag,
            },
        )

        def compute() -> dict | None:
            df = src.fetch_fundamentals_rows(ticker, as_of_date, period_type, lag)
            if df.empty:
                return None
            latest = df["report_date"].max()
            # defensive: the source filters 'TTM' but an upstream schema drift
            # could break that. Fail loudly rather than silently parsing.
            try:
                latest_period = datetime.fromisoformat(latest).date()
            except ValueError as e:
                raise ValueError(
                    f"unexpected non-ISO report_date {latest!r} for {ticker}"
                ) from e
            latest_rows = df[df["report_date"] == latest]
            items = {
                row.item_name: float(row.item_value)
                for row in latest_rows.itertuples(index=False)
            }
            return {
                "latest_period_end": latest_period,
                "items": items,
            }

        result = self.cache.get_or_compute(key, compute)
        if result is None:
            return None
        provenance = src.build_provenance(
            as_of_date=as_of_date,
            notes=f"get_fundamentals ticker={ticker} period_type={period_type}",
            fidelity_note=(
                f"Filing date proxy: period_end + {lag} days. True filing "
                "dates require SEC EDGAR (not yet wired). Late filers (real "
                "filing > proxy) will be incorrectly visible under this proxy."
            ),
        )
        return FundamentalsSnapshot(
            symbol=ticker,
            as_of_date=as_of_date,
            filing_lag_days=lag,
            latest_period_end=result["latest_period_end"],
            period_type=period_type,
            items=result["items"],
            provenance=provenance,
        )
