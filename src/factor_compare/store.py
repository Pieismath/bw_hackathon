"""KenFrenchFactorStore — drop-in PointInTimeDataStore for factor backtests.

The defeatbeta_yahoo data store is built around individual US equities
sourced from Yahoo Finance. That panel is unsuitable for any paper whose
cross-section is FACTOR PORTFOLIOS rather than stocks — most prominently
the AQR (2024) "Hidden Value of Streaky Returns" paper, which sorts the
153-factor JKP zoo by variance ratio.

This store is the lightweight bridge: it builds a synthetic price panel
by compounding the publicly-available Ken French monthly factor returns
(MOM + FF3 + FF5 → 6 factors), then exposes the panel through the same
three methods `run_backtest` calls on PointInTimeDataStore. The engine
runs unchanged; the variance_ratio signal computes per-factor VR; the
portfolio formation sorts the 6 factors into terciles (2 per bucket);
the result is a long-short between the two highest-VR and two lowest-VR
factors.

Six factors is fewer than AQR's 153 — but it's the right CONCEPTUAL
universe (factor portfolios, not stocks), the right TIME WINDOW (Mom
goes back to 1927-01, FF3 to 1926-07, FF5 to 1963-07), and survivorship-
bias-free (factors don't go bankrupt). The headline number won't match
AQR's 0.85 Sharpe like-for-like, but the run produces real, defensible
numbers from a real, free factor-zoo subset.

Why a separate class instead of extending PointInTimeDataStore: keeping
it isolated avoids tangling defeatbeta-specific paths (cache_dir, parquet
hf_datasets root, fundamentals filing lag) with factor-source paths.
The endpoint dispatches based on `spec.universe.name`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.data.store import QueryResult
from src.factor_compare.loader import (
    daily_to_monthly,
    load_ff3_monthly,
    load_ff5_daily,
    load_mom_daily,
)
from src.specs.provenance import ProvenanceRecord


# Universe name on `UniverseSpec.name` that triggers this store.
KEN_FRENCH_UNIVERSE_NAME = "ken_french_factors"

# Six KF factors used as the synthetic cross-section. Ordered for stable
# tercile assignment in the engine (form_portfolio sorts ascending by
# signal — the universe order doesn't affect that, but a stable ordering
# makes test fixtures reproducible).
KEN_FRENCH_FACTORS: tuple[str, ...] = (
    "Mkt-RF",
    "SMB",
    "HML",
    "RMW",
    "CMA",
    "Mom",
)


def _build_factor_panel() -> pd.DataFrame:
    """Compound the six KF factor returns into a synthetic month-end
    price panel anchored at 100. Monthly index, tickers as columns.

    SMB/HML/Mkt-RF come from the FF3 monthly file directly. MOM, RMW,
    and CMA are sourced from the daily files and compounded to monthly
    so all six series share a common month-end calendar. Months with
    any missing factor are dropped so the panel is rectangular.
    """
    ff3 = load_ff3_monthly()
    ff5_daily = load_ff5_daily()
    mom_daily = load_mom_daily()

    # FF3 monthly file is already keyed on month-end dates with decimal
    # returns — use Mkt-RF, SMB, HML directly.
    rets = pd.DataFrame({
        "Mkt-RF": ff3["Mkt-RF"],
        "SMB": ff3["SMB"],
        "HML": ff3["HML"],
        "RMW": daily_to_monthly(ff5_daily, "RMW"),
        "CMA": daily_to_monthly(ff5_daily, "CMA"),
        "Mom": daily_to_monthly(mom_daily, "Mom"),
    })

    # Normalize index to month-end DatetimeIndex matching the engine's
    # calendar convention (defeatbeta panel uses pandas month-end
    # timestamps with normalize()).
    rets.index = pd.to_datetime(rets.index)
    rets.index = rets.index.to_period("M").to_timestamp("M").normalize()

    # Drop months where any factor is missing so the variance_ratio
    # signal sees a rectangular panel (it can handle NaNs but the
    # cross-section integrity matters more than chasing 1926 history).
    rets = rets.dropna(how="any")

    # Compound to a synthetic price panel anchored at 100. The engine's
    # signal computation only cares about ratios (price[end]/price[start]
    # for past_return; pct_change for variance_ratio), so the anchor is
    # immaterial as long as it's positive.
    prices = 100.0 * (1.0 + rets).cumprod()
    return prices


@dataclass
class KenFrenchFactorStore:
    """Drop-in for PointInTimeDataStore on Ken French factor universes.

    Constructed lazily — the panel is built once on first access and
    cached in `_panel`. Subsequent backtests within the same process
    reuse the panel.
    """

    _panel: pd.DataFrame | None = None

    def _ensure_panel(self) -> pd.DataFrame:
        if self._panel is None:
            self._panel = _build_factor_panel()
        return self._panel

    def _provenance(self, as_of_date: date, notes: str = "") -> ProvenanceRecord:
        return ProvenanceRecord(
            source_id="ken_french_factor_library",
            source_tier="primary",
            as_of_date=as_of_date,
            fidelity_note=(
                "Synthetic price panel built from Ken French monthly factor "
                "returns (Mkt-RF, SMB, HML, RMW, CMA, Mom). Six factors is a "
                "small subset of the JKP 153-factor zoo the AQR paper sorts; "
                "concept-faithful but not like-for-like. No survivorship bias "
                "(factor portfolios don't go bankrupt)."
            ),
            notes=notes,
        )

    # ------------------------------------------------------------------
    # PointInTimeDataStore interface that run_backtest depends on
    # ------------------------------------------------------------------

    def get_monthly_close_panel(
        self,
        start_date: date,
        end_date: date,
    ) -> QueryResult[pd.DataFrame]:
        if end_date <= start_date:
            raise ValueError(
                f"end_date {end_date} must be after start_date {start_date}"
            )
        panel = self._ensure_panel()
        # iloc-style slice on the DatetimeIndex; .loc on date strings is
        # inclusive on both ends so we slice to month-end timestamps.
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        sliced = panel.loc[(panel.index >= start_ts) & (panel.index <= end_ts)]
        return QueryResult(
            data=sliced,
            as_of_date=end_date,
            provenance=self._provenance(
                end_date,
                notes=(
                    f"ken_french_factors panel: {len(sliced)} months × "
                    f"{len(KEN_FRENCH_FACTORS)} factors, "
                    f"{start_date} → {end_date}"
                ),
            ),
        )

    def get_universe(
        self,
        universe_name: str,
        as_of_date: date,
        min_price: float | None = None,
        min_history_days: int | None = None,
        recency_days: int = 14,
    ) -> QueryResult[list[str]]:
        # The factor universe is fixed and time-invariant — no PIT
        # filtering needed (factors don't get listed/delisted, and
        # min_price is meaningless for a return series). We accept the
        # filter args for interface compatibility but ignore them.
        panel = self._ensure_panel()
        # Filter to factors that have at least some history before
        # as_of_date so the signal warmup is honest.
        as_of_ts = pd.Timestamp(as_of_date)
        available = [
            f for f in KEN_FRENCH_FACTORS
            if f in panel.columns and panel.loc[panel.index <= as_of_ts, f].notna().any()
        ]
        return QueryResult(
            data=sorted(available),
            as_of_date=as_of_date,
            provenance=self._provenance(
                as_of_date,
                notes=(
                    f"universe={universe_name} (KF factor library) "
                    f"n={len(available)}/{len(KEN_FRENCH_FACTORS)}"
                ),
            ),
        )

    def get_market_cap_panel(self, start_date: date, end_date: date):
        # Factor portfolios don't have a market-cap concept. The engine
        # only calls this when spec.portfolio.weighting == "value", which
        # is meaningless for a factor cross-section — A1 should map AQR-
        # style specs to weighting="equal" and never reach this branch.
        raise NotImplementedError(
            "ken_french_factors universe doesn't support value weighting; "
            "use weighting='equal' on the portfolio spec"
        )

    def close(self):
        # Interface parity with DefeatBetaYahooSource (which closes
        # duckdb connections). This store has nothing to release.
        return None
