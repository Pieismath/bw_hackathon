"""Tests for src/data/ — the point-in-time data store and backing sources.

The PIT invariant section is the heart of the project: every test here must
hold before ANY agent or engine code can be trusted. Structure:

1. Snapshot glob helper (file discovery + multi/missing warnings)
2. Query-level cache (stable keys, round-trip, None-value safety)
3. PIT invariants (future-exclusion, pre-IPO, coverage boundaries, known values)
4. Universe (delisted handling, min_price, min_history)
5. Fundamentals PIT (filing lag proxy, TTM filter, per-query override)
6. Provenance wiring
"""

from __future__ import annotations

import time
import warnings
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.data.cache import QueryCache, make_cache_key
from src.data.sources.base import SnapshotSelectionWarning, resolve_defeatbeta_snapshot
from src.data.sources.defeatbeta_yahoo import DefeatBetaYahooSource
from src.data.store import FundamentalsSnapshot, PointInTimeDataStore, QueryResult

HF_ROOT = Path("data/cache/hf_datasets")
QUERY_CACHE_DIR = Path("data/cache/query_cache")


# ---------------------------------------------------------------------------
# 1. Snapshot glob helper
# ---------------------------------------------------------------------------

def test_resolve_snapshot_raises_when_root_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="snapshots"):
        resolve_defeatbeta_snapshot(tmp_path / "nowhere", required_files=["stock_prices.parquet"])


def test_resolve_snapshot_raises_when_no_snapshot(tmp_path: Path):
    base = tmp_path / "defeatbeta" / "datasets--defeatbeta--yahoo-finance-data" / "snapshots"
    base.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="no snapshots"):
        resolve_defeatbeta_snapshot(tmp_path, required_files=["stock_prices.parquet"])


def test_resolve_snapshot_raises_when_required_file_missing(tmp_path: Path):
    snap_data = (
        tmp_path / "defeatbeta" / "datasets--defeatbeta--yahoo-finance-data"
        / "snapshots" / "abc123" / "data"
    )
    snap_data.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="stock_prices.parquet"):
        resolve_defeatbeta_snapshot(tmp_path, required_files=["stock_prices.parquet"])


def test_resolve_snapshot_warns_when_multiple_picks_most_recent(tmp_path: Path):
    base = tmp_path / "defeatbeta" / "datasets--defeatbeta--yahoo-finance-data" / "snapshots"
    old = base / "old_hash" / "data"
    new = base / "new_hash" / "data"
    old.mkdir(parents=True)
    (old / "stock_prices.parquet").touch()
    time.sleep(0.02)  # ensure mtime differs on fast filesystems
    new.mkdir(parents=True)
    (new / "stock_prices.parquet").touch()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        data_dir = resolve_defeatbeta_snapshot(tmp_path, required_files=["stock_prices.parquet"])
    assert any(issubclass(w.category, SnapshotSelectionWarning) for w in caught)
    assert data_dir == new


def test_resolve_snapshot_real_snapshot_returns_data_dir():
    d = resolve_defeatbeta_snapshot(HF_ROOT, required_files=["stock_prices.parquet"])
    assert d.name == "data"
    assert (d / "stock_prices.parquet").exists()


# ---------------------------------------------------------------------------
# 2. Query-level cache
# ---------------------------------------------------------------------------

def test_cache_key_stable_across_arg_key_order():
    k1 = make_cache_key("src", "m", args={"a": 1, "b": 2, "c": 3})
    k2 = make_cache_key("src", "m", args={"c": 3, "b": 2, "a": 1})
    assert k1 == k2


def test_cache_key_changes_with_as_of_date():
    a = make_cache_key("src", "m", args={"as_of_date": date(2020, 1, 1)})
    b = make_cache_key("src", "m", args={"as_of_date": date(2020, 1, 2)})
    assert a != b


def test_cache_key_changes_with_tickers():
    a = make_cache_key("src", "m", args={"tickers": ["AAPL", "MSFT"]})
    b = make_cache_key("src", "m", args={"tickers": ["AAPL", "GE"]})
    assert a != b


def test_cache_key_stable_across_ticker_list_order():
    # The store is responsible for sorting; if caller forgets, key should still differ.
    # But when the store sorts, same-content sorted lists produce same key.
    a = make_cache_key("src", "m", args={"tickers": sorted(["AAPL", "MSFT", "GE"])})
    b = make_cache_key("src", "m", args={"tickers": sorted(["GE", "MSFT", "AAPL"])})
    assert a == b


def test_cache_miss_then_hit_computes_once(tmp_path: Path):
    cache = QueryCache(tmp_path / "qcache")
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"x": 42, "y": [1, 2, 3]}

    got1 = cache.get_or_compute("k1", compute)
    got2 = cache.get_or_compute("k1", compute)
    assert got1 == got2
    assert calls["n"] == 1


def test_cache_none_value_is_cached_not_recomputed(tmp_path: Path):
    cache = QueryCache(tmp_path / "qcache")
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return None

    cache.get_or_compute("kn", compute)
    cache.get_or_compute("kn", compute)
    assert calls["n"] == 1, "None values must be cached too (miss sentinel != None)"


def test_cache_roundtrips_dataframe(tmp_path: Path):
    cache = QueryCache(tmp_path / "qcache")
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    cache.get_or_compute("df1", lambda: df.copy())
    got = cache.get_or_compute("df1", lambda: pd.DataFrame({"a": []}))
    assert got.equals(df)


# ---------------------------------------------------------------------------
# 3. PIT invariants — the heart of the contract
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def store(tmp_path_factory):
    # Fresh query cache per module to keep tests deterministic.
    qcache_dir = tmp_path_factory.mktemp("pit_qcache")
    source = DefeatBetaYahooSource(cache_root=HF_ROOT)
    s = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": source},
        cache_dir=qcache_dir,
        fundamentals_filing_lag_days=90,
    )
    yield s
    source.close()


def test_pit_prices_excludes_future_dates(store):
    r = store.get_prices(["AAPL"], as_of_date=date(2015, 3, 15), lookback_days=30)
    assert isinstance(r, QueryResult)
    df = r.data
    assert not df.empty
    # report_date is VARCHAR in ISO format; lexical compare is chronological.
    assert df["report_date"].max() <= "2015-03-15"


def test_pit_prices_pre_ipo_returns_empty(store):
    # TSLA first price is 2010-06-29; 2009-06-30 is pre-IPO.
    r = store.get_prices(["TSLA"], as_of_date=date(2009, 6, 30), lookback_days=365)
    assert r.data.empty
    assert r.as_of_date == date(2009, 6, 30)  # provenance still populated


def test_pit_prices_before_all_coverage_returns_empty(store):
    # Coverage begins 1994-11-30 across the whole file.
    r = store.get_prices(["AAPL", "MSFT"], as_of_date=date(1980, 1, 1), lookback_days=365)
    assert r.data.empty


def test_pit_prices_at_coverage_boundary(store):
    # 1994-11-30 is the earliest date — a 1-day lookback should return exactly that row.
    r = store.get_prices(["AAPL"], as_of_date=date(1994, 11, 30), lookback_days=1)
    assert not r.data.empty
    assert (r.data["report_date"] == "1994-11-30").any()


def test_pit_prices_multiple_tickers_all_respect_as_of(store):
    r = store.get_prices(
        ["AAPL", "MSFT", "GE", "XOM"],
        as_of_date=date(2020, 6, 30),
        lookback_days=10,
    )
    assert set(r.data["symbol"].unique()) == {"AAPL", "MSFT", "GE", "XOM"}
    assert r.data["report_date"].max() <= "2020-06-30"
    assert r.data["report_date"].min() >= "2020-06-20"


def test_pit_prices_known_aapl_close(store):
    # Sanity check against a publicly known close — AAPL 2020-06-30 closed near $91.20.
    r = store.get_prices(["AAPL"], as_of_date=date(2020, 6, 30), lookback_days=1)
    row = r.data[r.data["report_date"] == "2020-06-30"].iloc[0]
    assert float(row["close"]) == pytest.approx(91.20, abs=0.10)


def test_pit_prices_lookback_window_truncates_not_extends(store):
    r = store.get_prices(["AAPL"], as_of_date=date(2020, 6, 30), lookback_days=5)
    assert not r.data.empty
    # Lookback of 5 calendar days → at most 5 rows (often fewer due to weekends).
    assert len(r.data) <= 5


def test_pit_prices_cache_hit_returns_equal(store):
    r1 = store.get_prices(["AAPL"], as_of_date=date(2020, 6, 30), lookback_days=3)
    r2 = store.get_prices(["AAPL"], as_of_date=date(2020, 6, 30), lookback_days=3)
    assert r1.data.equals(r2.data)
    # Also confirm ticker-order invariance
    r3 = store.get_prices(["MSFT", "AAPL"], as_of_date=date(2020, 6, 30), lookback_days=3)
    r4 = store.get_prices(["AAPL", "MSFT"], as_of_date=date(2020, 6, 30), lookback_days=3)
    assert r3.data.sort_values(["symbol", "report_date"]).reset_index(drop=True).equals(
        r4.data.sort_values(["symbol", "report_date"]).reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# 4. Universe
# ---------------------------------------------------------------------------

def test_universe_excludes_pre_ipo_tsla(store):
    u = store.get_universe("defeatbeta_all_equities", as_of_date=date(2009, 6, 30))
    assert "TSLA" not in u.data
    assert "AAPL" in u.data


def test_universe_includes_tsla_after_ipo(store):
    u = store.get_universe("defeatbeta_all_equities", as_of_date=date(2011, 6, 30))
    assert "TSLA" in u.data


def test_universe_min_price_filters(store):
    lo = store.get_universe("defeatbeta_all_equities", as_of_date=date(2020, 6, 30), min_price=5.0)
    hi = store.get_universe("defeatbeta_all_equities", as_of_date=date(2020, 6, 30), min_price=500.0)
    assert len(hi.data) < len(lo.data)
    assert "TSLA" in lo.data  # TSLA was well above $5 on 2020-06-30


def test_universe_min_history_filters(store):
    # Require 180 days of history as of 2021-01-15.
    # RBLX IPO'd 2021-03-10, so it can't satisfy min_history_days=180 on 2021-01-15.
    u = store.get_universe(
        "defeatbeta_all_equities",
        as_of_date=date(2021, 1, 15),
        min_history_days=180,
    )
    assert "RBLX" not in u.data
    assert "AAPL" in u.data


def test_universe_unknown_name_rejected(store):
    with pytest.raises(ValueError, match="unknown universe"):
        store.get_universe("mars_market", as_of_date=date(2020, 1, 1))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 5. Fundamentals PIT
# ---------------------------------------------------------------------------

def test_fundamentals_ttm_rows_never_leak(store):
    # 'TTM' is stored as a literal string in the report_date column.
    # Even at max as_of_date, latest_period_end must be a real ISO date.
    snap = store.get_fundamentals("AAPL", as_of_date=date(2024, 12, 31), period_type="annual")
    assert snap is not None
    # If we got a row, its period_end must be a real date object (not string "TTM").
    assert isinstance(snap.latest_period_end, date)


def test_fundamentals_filing_lag_excludes_too_new(store):
    # With 90-day lag, nothing reported less than 90 days before as_of_date is visible.
    snap = store.get_fundamentals("AAPL", as_of_date=date(2021, 12, 31), period_type="annual")
    assert snap is not None
    assert (date(2021, 12, 31) - snap.latest_period_end).days >= 90
    assert snap.filing_lag_days == 90


def test_fundamentals_per_query_lag_override_is_respected(store):
    snap = store.get_fundamentals(
        "AAPL",
        as_of_date=date(2021, 12, 31),
        period_type="annual",
        filing_lag_days=30,
    )
    assert snap is not None
    assert snap.filing_lag_days == 30
    # The 30-day version can include more recent period ends than the 90-day default.
    assert (date(2021, 12, 31) - snap.latest_period_end).days >= 30


def test_fundamentals_absent_if_as_of_before_first_filing(store):
    # Coverage starts 2019-05-31. With 90-day lag, first visible as_of = 2019-08-29.
    snap = store.get_fundamentals("AAPL", as_of_date=date(2019, 1, 1), period_type="annual")
    assert snap is None


def test_fundamentals_returns_known_item_names(store):
    snap = store.get_fundamentals("AAPL", as_of_date=date(2023, 12, 31), period_type="annual")
    assert snap is not None
    # We expect at least the Novy-Marx building blocks to be present.
    for item in ("total_revenue", "total_assets"):
        assert item in snap.items, f"expected {item} in items, got {list(snap.items.keys())[:10]}"


# ---------------------------------------------------------------------------
# 6. Provenance wiring
# ---------------------------------------------------------------------------

def test_prices_provenance_carries_as_of_and_source(store):
    r = store.get_prices(["AAPL"], as_of_date=date(2020, 6, 30), lookback_days=1)
    assert r.provenance.source_id == "defeatbeta_yahoo"
    assert r.provenance.source_tier == "primary"
    assert r.provenance.as_of_date == date(2020, 6, 30)


def test_universe_provenance_flags_survivorship(store):
    u = store.get_universe("defeatbeta_all_equities", as_of_date=date(2020, 6, 30))
    assert u.provenance.source_id == "defeatbeta_yahoo"
    # Yahoo drops delisted — store must surface this on the universe query.
    note = (u.provenance.fidelity_note or "") + u.provenance.notes
    assert "survivorship" in note.lower()


def test_fundamentals_provenance_names_filing_proxy(store):
    snap = store.get_fundamentals("AAPL", as_of_date=date(2023, 12, 31), period_type="annual")
    assert snap is not None
    note = (snap.provenance.fidelity_note or "") + snap.provenance.notes
    assert "proxy" in note.lower() or "filing" in note.lower()
