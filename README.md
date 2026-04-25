# Paper Replication Machine

Multi-agent system that takes a quantitative finance research paper plus data and replicates its key quantitative results end-to-end, with structured reporting on methodological ambiguity, sensitivity, and implementability.

## Status

**Phases 1–4 complete.** Phase 1 delivers the canonical backtest engine, point-in-time data store, and structured specs. Phase 2 adds PDF parsing and the A1 (methodology extractor) + A2 (quote verifier) + A3 (adversarial reviewer) agent layer. Phase 3 adds D1 (result comparator) and D2 (divergence diagnostician — LLM-guided spec mutations validated by engine reruns). Phase 4 adds the robustness battery (six stress-test families) and D3 (robustness adversary → implementability verdict, signal-type classification, gap attribution). A FastAPI + SPA MVP at `app/` renders every artifact in a dark-mode research-lab UI including Robustness and Diagnosis tabs.

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

Extending `PortfolioSpec.construction` to support `size_x_return_2x3` and adding a universe-level dollar-volume liquidity filter would close the VW gap. The construction-mismatch and liquidity-filter follow-ups are tracked in `LIMITATIONS.md` and are partially exercised by the `liquidity` and `subperiod` families of the existing robustness battery.

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
│   ├── signals.py  past_return + variance_ratio (AQR streakiness) signals
│   ├── portfolio.py quantile sorts, EW/VW weighting
│   ├── tranches.py overlapping-tranche bookkeeping
│   ├── costs.py    (2 × gross / K) × bps / 10000 per-month cost
│   ├── metrics.py  Newey-West (lag = K-1), Sharpe, drawdown
│   └── backtest.py run_backtest(spec, store) → BacktestResult
├── pdf/            pdfplumber parser + deterministic quote verifier
├── robustness/     Phase 4 stress-test battery — one module per family
│   ├── battery.py    driver: runs every family, aggregates RobustnessScorecard
│   ├── lag.py        execution-lag sweep + alpha half-life
│   ├── costs.py      transaction-cost sweep + cost_threshold_bps
│   ├── subperiod.py  decade / regime / rolling subperiod stability
│   ├── liquidity.py  min-price / dollar-volume filter sweep
│   ├── capacity.py   ADV-based implementation-shortfall AUM estimate
│   └── data_quality.py  PIT contamination + survivorship flags
├── agents/
│   ├── extraction/ A1 methodology_extractor (Sonnet), A2 extraction_verifier
│   │               (Haiku support-check + deterministic fuzzy match),
│   │               A3 adversarial_reviewer (Sonnet, exactly 3 criticisms)
│   ├── implementation/ B1 data_mapper + B2 mapping_verifier (Sonnet/Haiku)
│   ├── validation/ D1 result_comparator (deterministic),
│   │               D2 divergence_diagnostician (Sonnet, LLM-proposed
│   │               mutations validated by engine reruns),
│   │               D3 robustness_adversary (Sonnet, implementability verdict)
│   └── prompts/    system prompts as .md files (diff-reviewable)
└── utils/llm.py    Anthropic wrapper: structured tool-use, schema retry,
                    disk cache keyed on (model, prompt, content, schema)

app/                 MVP FastAPI + static SPA
├── main.py         /api/demo, /api/papers, /api/extract, /api/critique,
│                   /api/backtest, /api/robustness, /api/diagnose
├── demo_fixture.py schema-valid JT-1993 bundle including robustness + diagnosis
└── static/         index.html + styles.css + app.js (no build step)

scripts/
├── phase1_gate.py          regression harness vs Ken French MOM
├── run_jt_end_to_end.py    Phase 2 top-to-bottom demo on JT 1993
├── run_a1_on_jt.py         A1-only run for prompt iteration
├── run_d2_on_jt.py         D2 divergence diagnostician on JT gap
├── run_phase4_on_jt.py     robustness battery + D3 judgment on JT
├── run_phase4_postfix.py   post-fix robustness rerun after spec mutation
├── run_jt_full_pipeline.py A1 → A2 → A3 → engine → D1 → D2 → battery → D3
├── run_oos_dbt_1985.py     out-of-sample DeBondt-Thaler 1985 reversal demo
└── mentor_demo.py          top-to-bottom narrative demo for mentor review
```

Phase 5 (report synthesizer E1, tradeability scorecard, paper-vs-replication diff, news contextualization) remains planned — see the Roadmap section below.

## MVP web UI

The `app/` package wraps the Phase 1/2 surface area in a single-page dark-mode UI:

- **Run config panel** — overrides `universe.min_price`, sample window, JT grid, weighting, buckets, costs, execution lag; live-updates the Spec tab.
- **Overview** — PDF upload, papers list, KPI grid, swarm-telemetry log.
- **Spec** — every `ReplicationSpec` field with its verbatim `SupportingQuote` and ambiguity table, plus the paper-reported `HeadlineClaim` (monthly_return + t_stat + window_label) when A1 extracted one.
- **Verification** — A2 checks (status, page, confidence, Haiku support verdict, PASS/FAIL). The headline-claim quote is verified at high severity alongside the methodology quotes.
- **Critique** — A3 criticisms color-coded by severity with remediation.
- **Backtest** — paper-vs-replication gap table, engine metrics, **post-formation signal-decay chart** (gross per-tranche return by months since formation with ±1 SE whiskers — the "how long is the signal alive?" plot), interactive cumulative-return chart (drag-to-zoom, double-click to reset, hover tooltip with monthly/cumulative/drawdown values, regime shading for the 2001 reversal + 2009 momentum crash), stacked drawdown subpanel, recent returns.
- **Robustness** — D3 implementability verdict, signal-type classification, fragility bullets, side-by-side decay-curve charts for the lag and cost families (with markers at `lag_half_life_days` and `cost_threshold_bps`), and the full stress-test scorecard (lag / cost / subperiod / liquidity / capacity / data-quality) with surviving vs failed rows.
- **Diagnosis** — D2 primary cause + evidence, gap-closure waterfall showing pre→post `|paper − replication|` for each mutation (green = closed, red = widened), per-mutation `MutationResult` rows, alternatives ruled out, residual-gap attribution. D2 fires automatically after the backtest when A1 extracted a `HeadlineClaim`; when it hasn't, the tab shows the claim that D2 will compare against (or explains why D2 is skipped).
- **Lineage** — interactive drill-down across the full bundle. Pipeline stages with click-to-jump, primary data sources + quality flags, a searchable index of every `SupportingQuote` (filter by text / page / field), and the D2 mutation chain. Replaces the previous flat Provenance card view.

Endpoints: `/api/demo` (no deps), `/api/papers` (GET/POST), `/api/extract`, `/api/critique`, `/api/backtest`, `/api/robustness`, `/api/diagnose`. Error responses are structured JSON with an actionable `hint` field; the frontend surfaces the raw body if a response isn't JSON.

Every endpoint that drives the engine (`/api/backtest`, `/api/robustness`, `/api/diagnose`, plus the orchestrated `/api/pipeline/start`) runs two pre-engine substitutions on the incoming spec: `_clip_spec_to_data_window` clips out-of-range date windows (e.g. JT-1993's 1965–1989 pre-defeatbeta-panel window), and `_engine_kind_fallback` short-circuits for the natively-supported signal kinds (`past_return`, `variance_ratio`) and substitutes a 12-month-momentum proxy for the unsupported ones (`fundamental_ratio`, `custom`). The `variance_ratio` kind computes the AQR streakiness statistic — `Var(rolling_12m_return) / (12 × Var(monthly_return))` per ticker over a 60-month default window — and sorts the cross-section by the result; high VR ⇒ streaky / momentum-like, low VR ⇒ mean-reverting. The AQR paper sorts JKP factor portfolios with this statistic while we sort individual equities; the cross-section difference is surfaced via a `data_quality_flag` rather than smuggled through. Both substitutions emit a `data_quality_flag` on the resulting `BacktestResult`, surfaced in the UI as a yellow banner, so the swap stays honest.

`extract_and_verify` (A1+A2 orchestration) also runs a self-consistency check on `spec.headline_claim`: a non-zero Newey-West t-stat paired with `monthly_return = 0.0` is mathematically impossible (`t = mean·√N / σ`), so when A1 emits this combination — observed on AQR Streaks of Daily Returns, where the abstract reports a t-stat but the paper expresses the headline as a Sharpe ratio — the orchestrator drops `headline_claim` to `None` and notes it on the spec rather than letting D2 fire against a phantom zero. The frontend renders `not extracted` instead of `+0.000%/mo` for any claim that hits this shape.

When the engine substituted `signal.kind` (`custom`/`fundamental_ratio` papers run as a 12-month-momentum proxy) OR when no headline claim exists (A1 missed it and no manual override was supplied), the verdict strip stops claiming "implementable alpha vs paper" and switches to two structural-validity tiers: `PROXY ONLY` (engine ran a substitute strategy, every number is a verdict on the proxy not the paper) and `NO PAPER TARGET` (engine ran the paper's strategy but there's no claim to compare against, so the alpha number is vs zero). The Backtest tab paints a fail-severity banner at the top in proxy mode listing every metric on the page that's a proxy result. The Overview tab's Run config panel includes three optional headline-override inputs (`monthly return %/mo`, `t-stat`, `window`) so users can manually supply the paper's number when A1 fails to extract it — common for papers reporting Sharpe ratios or regression alphas.

See `DESIGN_NOTES.md` for non-obvious design decisions and bug postmortems, and `CLAUDE.md` for agent instructions on how to extend this repo.

## Roadmap (Phase 5+)

Phases 3 and 4 already shipped: the D2 divergence diagnostician (LLM-proposed spec mutations validated by engine reruns) is live in `src/agents/validation/divergence_diagnostician.py`, and the six-family robustness battery (`lag`, `costs`, `subperiod`, `liquidity`, `data_quality`, `capacity`) plus the D3 robustness adversary are live in `src/robustness/` and `src/agents/validation/robustness_adversary.py`. Both are exposed via `/api/diagnose` and `/api/robustness` and rendered in the Diagnosis and Robustness tabs.

Still planned, on top of the Phase 1–4 foundation:

- **Report Synthesizer (E1)** — single self-contained HTML artifact stitching spec + verification + critique + backtest + diagnosis + robustness with click-through provenance back to the underlying `SupportingQuote`s and `ProvenanceRecord`s. This is overview.md Step 13 and the natural next deliverable now that every upstream agent emits a typed spec.
- **Tradeability Scorecard** — compute turnover, capacity, rebalance frequency, and gross/net spread from `BacktestResult`, then run an LLM heuristic (Sonnet) that emits a CIO-style verdict — `tradeable` / `borderline` / `not tradeable` — with reasons tied to the underlying metrics. Distinct from D3's implementability verdict in that it focuses on portfolio-construction realism rather than fragility-under-stress.
- **Paper-vs-replication diff** — A1 now extracts `HeadlineClaim` (`src/specs/paper_metrics.py`) with the paper's self-reported `monthly_return` + `t_stat` + `window_label` plus a verbatim `SupportingQuote` that A2 verifies at high severity. D2 picks this up automatically as its comparison target. The remaining piece is a side-by-side "paper says X, we got Y, delta = Δ" table on the Backtest tab — the data is already on the bundle as `verified_spec.spec.headline_claim` and `paper_claim` (the projected flat shape that drives D2).
- **News contextualization (guarded)** — optional tab that pulls headlines around the largest drawdown windows. Needs an A2-style verifier (every headline must cite a retrievable URL + publication date inside the drawdown window) because a raw LLM will happily invent plausible stories. Gated behind an env flag; off by default.
