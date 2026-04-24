# Paper Replication Machine

Multi-agent system that takes a quantitative finance research paper plus data and replicates its key quantitative results end-to-end, with structured reporting on methodological ambiguity, sensitivity, and implementability.

## Status

**Phase 1 + Phase 2 complete.** Phase 1 delivers the canonical backtest engine, point-in-time data store, and structured specs. Phase 2 adds PDF parsing and the A1 (methodology extractor) + A2 (quote verifier) + A3 (adversarial reviewer) agent layer. A FastAPI + SPA MVP at `app/` renders every Phase 1/2 artifact in a dark-mode research-lab UI.

## Quick start

```bash
# 1. Install + activate venv (setup.sh handles this), then:
.venv/bin/python -m pytest tests/ -q                     # 85 tests, ~2 min
.venv/bin/python -u scripts/phase1_gate.py               # engine validation

# 2. MVP web UI:
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000 → click LOAD DEMO (no API key required)
```

`LOAD DEMO` renders a fully schema-valid Jegadeesh-Titman 1993 bundle (spec, A2 report, A3 critique, 312 monthly backtest obs, provenance). To run the real pipeline on an uploaded PDF you need `ANTHROPIC_API_KEY` in `.env`; for `/api/backtest` you also need the defeatbeta parquet cache at `data/cache/hf_datasets/`.

## Models

All three extraction agents run on **Sonnet 4.6** (`claude-sonnet-4-6`). A2 support-checks and B2 mapping verification run on **Haiku 4.5** (`claude-haiku-4-5-20251001`). Previous versions used Opus 4.7 on A1/A3/B1 — swapped to Sonnet to stay inside typical org TPM limits on long papers (Fama-French 1993 hits ~45k input tokens in a single call, which blows a 30k-TPM Opus quota).

Model IDs live in one place per agent (`MODEL = "..."` at the top of each module). Swap back to Opus 4.7 by changing those constants if you have the quota and want the extra quality on A3 critiques.

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

## Architecture

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
├── pdf/            pdfplumber parser + deterministic quote verifier
├── agents/
│   ├── extraction/ A1 methodology_extractor (Sonnet), A2 extraction_verifier
│   │               (Haiku support-check + deterministic fuzzy match),
│   │               A3 adversarial_reviewer (Sonnet, exactly 3 criticisms)
│   ├── implementation/ B1 data_mapper scaffold (Sonnet)
│   └── prompts/    system prompts as .md files (diff-reviewable)
└── utils/llm.py    Anthropic wrapper: structured tool-use, schema retry,
                    disk cache keyed on (model, prompt, content, schema)

app/                 MVP FastAPI + static SPA
├── main.py         /api/demo, /api/papers, /api/extract, /api/critique, /api/backtest
├── demo_fixture.py schema-valid JT-1993 bundle for the /api/demo endpoint
└── static/         index.html + styles.css + app.js (no build step)

scripts/
├── phase1_gate.py          regression harness vs Ken French MOM
├── run_jt_end_to_end.py    Phase 2 top-to-bottom demo on JT 1993
└── run_a1_on_jt.py         A1-only run for prompt iteration
```

Phases 3–5 add robustness battery, divergence diagnostician, and report synthesizer on top of this foundation.

## MVP web UI

The `app/` package wraps the Phase 1/2 surface area in a single-page dark-mode UI:

- **Overview** — PDF upload, papers list, KPI grid, swarm-telemetry log.
- **Spec** — every `ReplicationSpec` field with its verbatim `SupportingQuote` and ambiguity table.
- **Verification** — A2 checks (status, page, confidence, Haiku support verdict, PASS/FAIL).
- **Critique** — A3 criticisms color-coded by severity with remediation.
- **Backtest** — paper-vs-replication gap table, engine metrics, inline SVG cumulative-return chart, recent returns.
- **Provenance** — the `ProvenanceRecord` lineage with parent IDs and fidelity notes.

Endpoints: `/api/demo` (no deps), `/api/papers` (GET/POST), `/api/extract`, `/api/critique`, `/api/backtest`. Error responses are structured JSON with an actionable `hint` field; the frontend surfaces the raw body if a response isn't JSON.

See `DESIGN_NOTES.md` for non-obvious design decisions and bug postmortems, and `CLAUDE.md` for agent instructions on how to extend this repo.

## Roadmap (Phase 3+)

Planned features on top of the Phase 1+2 foundation:

- **Tradeability Scorecard** — compute turnover, capacity, rebalance frequency, and gross/net spread from `BacktestResult`, then run an LLM heuristic (Sonnet) that emits a CIO-style verdict — `tradeable` / `borderline` / `not tradeable` — with reasons tied to the underlying metrics.
- **Robustness / decay stress test** — re-run `run_backtest` with the signal lagged 1/2/5/10 days and sliced by regime (pre-2008, 2008 crisis, 2010s, 2020–2022). Render a heatmap in a new Robustness tab; decay speed is a direct proxy for alpha fragility.
- **Paper-vs-replication diff** — have A1 extract the paper's self-reported Sharpe / mean return / t-stat (with supporting quotes) into a new `PaperReportedMetrics` spec, then render a side-by-side "paper claims X, we got Y, delta = Δ" table on the Backtest tab. This is the single most legible artifact for a reviewer.
- **News contextualization (guarded)** — optional tab that pulls headlines around the largest drawdown windows. Needs an A2-style verifier (every headline must cite a retrievable URL + publication date inside the drawdown window) because a raw LLM will happily invent plausible stories. Gated behind an env flag; off by default.
