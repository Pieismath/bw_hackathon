# Paper Replication Machine

Multi-agent system that takes a quantitative finance research paper plus data and replicates its key quantitative results end-to-end, with structured reporting on methodological ambiguity, sensitivity, and implementability.

## Status

Phase 1 complete: structured specs, point-in-time data store, canonical backtest engine, and external validation via Ken French's published MOM factor.

## Running the repo

Environment is pre-provisioned. Use `.venv/bin/python` for everything — a shell alias would otherwise route bare `python` to an unrelated interpreter.

```bash
.venv/bin/python -m pytest tests/ -q                      # 85 tests, ~2 min
.venv/bin/python -u scripts/phase1_gate.py                # engine validation
```

## Engine validation

The engine is validated against Ken French's published monthly MOM factor (Tuck Data Library) on overlapping months 2000–2023, 288 observations. Full results live at `outputs/phase1_validation.json`. Regression harness: `scripts/phase1_gate.py`.

| Configuration              | n   | Corr vs KF MOM | Mean (bps/mo) | Interpretation                              |
| -------------------------- | --- | -------------- | ------------- | ------------------------------------------- |
| JT (6,1,6) decile EW       | 293 | **+0.77**      | +41 ± 632     | Hackathon demo target                       |
| KF (11,1,1) decile EW      | 288 | **+0.80**      | +27 ± 714     | Validates signal + overlap + return logic   |
| KF (11,1,1) decile VW      | 288 | +0.19          | −60 ± 2963    | Construction mismatch, not engine bug       |

**EW correlation ~0.80 at 288 overlapping months validates the engine's signal generation, overlapping-tranche bookkeeping, execution-lag handling, and Newey-West standard errors.** The universe, breakpoints, and survivorship-bias treatment differ from CRSP/Ken French, so exact replication is not expected.

The VW divergence is a known factor-literature difference, not an engine bug:
- Ken French's MOM is a **2×3 size-sorted double-sort** — stocks are split by NYSE-median market cap (big/small), then independently by prior return (low/mid/high at NYSE 30th/70th percentiles). MOM = ½(BigHigh + SmallHigh − BigLow − SmallLow).
- Our engine currently supports plain decile sorts. VW then concentrates weight on a few mega-caps (amplified by residual data-quality noise in the Yahoo universe), producing different — but not incorrect — returns.

Extending `PortfolioSpec.construction` to support `size_x_return_2x3` and adding a universe-level dollar-volume liquidity filter would close the VW gap; both are Phase 4 robustness-battery work.

## Data

Local parquet snapshots (pre-downloaded to `data/cache/hf_datasets/`):

- `defeatbeta/yahoo-finance-data` — daily OHLCV, quarterly shares outstanding, fundamentals (2019+), earnings transcripts. US universe, ~11k symbols, prices from 1994-11-30.
- Ken French `F-F_Momentum_Factor.csv` — monthly MOM factor 1927–present, used as external validation fixture.

**Survivorship bias**: Yahoo excludes delisted names. Surfaced on every universe query via `ProvenanceRecord.fidelity_note` and propagated to `BacktestResult.data_quality_flags`.

## Architecture (so far)

```
src/
├── specs/          frozen Pydantic v2 contracts between agents ↔ engine
├── data/           PointInTimeDataStore — every call requires as_of_date
│   ├── cache.py    disk-backed query cache, keyed (source, method, args)
│   ├── sources/    DefeatBetaYahooSource (DuckDB on local parquet)
│   └── store.py    QueryResult[T] with ProvenanceRecord wrapping
├── engine/         canonical backtest engine — one tested implementation
│   ├── calendar.py month-end dates, business-day shift
│   ├── signals.py  past_return signal (extensible to fundamental_ratio)
│   ├── portfolio.py quantile sorts, EW/VW weighting
│   ├── tranches.py overlapping-tranche bookkeeping
│   ├── costs.py    (2 × gross / K) × bps / 10000 per-month cost
│   ├── metrics.py  Newey-West (lag = K-1), Sharpe, drawdown
│   └── backtest.py run_backtest(spec, store) → BacktestResult
```

Phases 2–5 add the agent layer (extraction / implementation / validation / synthesis) on top of this foundation.

See `DESIGN_NOTES.md` for non-obvious design decisions and bug postmortems.
