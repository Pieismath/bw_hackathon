# Paper Replication Machine

Multi-agent system that takes a quantitative finance research paper plus data and replicates its key quantitative results end-to-end, with structured reporting on methodological ambiguity, sensitivity, and implementability.

## Status

**Phases 1–4 complete.** Phase 1 delivers the canonical backtest engine, point-in-time data store, and structured specs. Phase 2 adds PDF parsing and the A1 (methodology extractor) + A2 (quote verifier) + A3 (adversarial reviewer) agent layer. Phase 3 adds D1 (result comparator) and D2 (divergence diagnostician — LLM-guided spec mutations validated by engine reruns). Phase 4 adds the robustness battery (six stress-test families) and D3 (robustness adversary → implementability verdict, signal-type classification, gap attribution). A FastAPI + SPA MVP at `app/` renders every artifact in a Bridgewater-style intelligence-briefing terminal (institutional dark mode, brick-red and muted-gold accents, serif headers + sans-serif data, no AI / snake_case identifiers leaked into the surface) including Robustness and Diagnosis tabs.

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
├── pdf/            pdfplumber parser (pypdfium2 fallback for glyph-spaced OCR) + deterministic quote verifier
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
├── run_all_pipelines.py    audit harness — runs `app.main._run_pipeline` on
│                           every PDF in `data/papers/` and writes the resulting
│                           bundle to `outputs/audit/<paper_id>.json`. Optional
│                           positional args restrict the run to specific stems.
└── mentor_demo.py          top-to-bottom narrative demo for mentor review
```

Phase 5 (report synthesizer E1, tradeability scorecard, paper-vs-replication diff, news contextualization) remains planned — see the Roadmap section below.

## MVP web UI

The `app/` package wraps the Phase 1/2 surface area in a single-page **Bridgewater Intelligence Briefing** UI — institutional research-terminal aesthetic on a deep-obsidian background, brick-red `#8b231c` and muted-gold `#b89762` accents, hairline `#2d333b` borders, sharp 0–2 px corners, Georgia serif headers / Inter sans-serif tabular data, no glow / gradient / rounded-pill effects. A `terminologyMap` in `app/static/app.js` translates every snake_case schema field into professional finance prose (`monthly_return` → "Monthly Excess Return", `t_stat` → "t-statistic (Newey-West)", etc.) before it reaches the DOM, and a discrete Bridgewater wordmark sits bottom-right as an attribution badge.

- **Run config panel** — overrides `universe.min_price`, sample window, JT grid, weighting, buckets, costs, execution lag; live-updates the Spec tab.
- **Overview** — PDF upload, papers list, KPI grid, swarm-telemetry log.
- **Spec** — every `ReplicationSpec` field with its verbatim `SupportingQuote` and ambiguity table, plus the paper-reported `HeadlineClaim` (monthly_return + t_stat + window_label) when A1 extracted one.
- **Verification** — A2 checks (status, page, confidence, Haiku support verdict, PASS/FAIL). The headline-claim quote is verified at high severity alongside the methodology quotes.
- **Critique** — A3 criticisms color-coded by severity with remediation.
- **Backtest** — paper-vs-replication gap table, engine metrics, **post-formation signal-decay chart** (gross per-tranche return by months since formation with ±1 SE whiskers — the "how long is the signal alive?" plot), interactive cumulative-return chart (drag-to-zoom, double-click to reset, hover tooltip with monthly/cumulative/drawdown values, regime shading for the 2001 reversal + 2009 momentum crash), stacked drawdown subpanel, recent returns.
- **Robustness** — D3 implementability verdict, signal-type classification, fragility bullets, side-by-side decay-curve charts for the lag and cost families (with markers at `lag_half_life_days` and `cost_threshold_bps`), and the full stress-test scorecard (lag / cost / subperiod / liquidity / capacity / data-quality) with surviving vs failed rows. If the D3 LLM call fails (529 overload, missing API key, validation error), `judge` returns a deterministic fallback `RobustnessJudgment` (`implementable_alpha` = baseline.mean_return, `signal_type="not_evaluated"`, `gap_attribution="unexplained"`, `confidence="low"`, fragility signals copied straight from the scorecard) so the verdict strip still renders — mirroring D2's `_fallback_diagnosis` policy.
- **Diagnosis** — D2 primary cause + evidence, gap-closure waterfall showing pre→post `|paper − replication|` for each mutation (green = closed, red = widened), per-mutation `MutationResult` rows, alternatives ruled out, residual-gap attribution. D2 fires automatically after the backtest when A1 extracted a `HeadlineClaim`; when it hasn't, the tab shows the claim that D2 will compare against (or explains why D2 is skipped).
- **Lineage** — interactive drill-down across the full bundle. Pipeline stages with click-to-jump, primary data sources + quality flags, a searchable index of every `SupportingQuote` (filter by text / page / field), and the D2 mutation chain. Replaces the previous flat Provenance card view.

Endpoints: `/api/demo` (no deps), `/api/papers` (GET/POST), `/api/extract`, `/api/critique`, `/api/backtest`, `/api/robustness`, `/api/diagnose`. Error responses are structured JSON with an actionable `hint` field; the frontend surfaces the raw body if a response isn't JSON.

Every endpoint that drives the engine (`/api/backtest`, `/api/robustness`, `/api/diagnose`, plus the orchestrated `/api/pipeline/start`) calls **`prepare_spec_for_engine(spec)`** in `app/main.py` — the single canonical post-A1 prep chain that bundles store dispatch (`spec.universe.name` → defeatbeta or Ken French), window clipping / out-of-sample substitution, `signal.kind` proxy fallback (only fires for `custom` after Phase E), `signal.lookback_months` clamp, `portfolio.weighting` fallback (signal_weighted → equal), and Ken-French-universe portfolio coercion. Each substitution emits a typed **`SpecAdaptation`** record (one of `signal_kind_proxy_substitution`, `lookback_clamp`, `weighting_fallback`, `portfolio_bucket_coercion`, `out_of_sample_engine_window`, `window_clipped_to_panel`, `fundamentals_unavailable_in_window`, `unknown_fundamental_ratio`, `engine_does_not_expose_per_leg`) attached to `BacktestResult.spec_adaptations` — replacing the earlier string-flag idiom that the frontend had to substring-match. Two universes wired today: `defeatbeta_all_equities` (US stocks, post-1994, Yahoo-source, survivorship-biased) and `ken_french_factors` (KF 6-factor zoo: Mkt-RF, SMB, HML, RMW, CMA, Mom — back to 1973, no survivorship bias). Three signal kinds are natively implemented: `past_return`, `variance_ratio` (AQR streakiness: `Var(rolling-12m return) / (12 × Var(monthly return))`), and (Phase E) `fundamental_ratio` via a five-entry registry (`gross_profitability`, `book_to_market`, `earnings_yield`, `asset_growth`, `accruals`) that resolves line items through any `FundamentalDataSource`-conforming store. Only `custom` triggers proxy substitution.

`HeadlineClaim` (`src/specs/paper_metrics.py`) is a **Pydantic v2 discriminated union over six variants** (Phase B): `MonthlyLongShortReturn`, `NestedConditionalSortReturn`, `SharpeRatioDifference`, `VarianceRatioStatistic`, `RegressionAlpha`, `StatisticalTestClaim`. A1's prompt (`methodology_extractor.md` rule 8) dispatches the paper into the right variant based on the paper's headline SHAPE — a momentum paper's single L/S return, a nested-conditioning paper's inner-cell return, a factor-zoo paper's Sharpe-spread, a VR random-walk-test statistic, a factor-model regression intercept, or a generic test-statistic catch-all. The frontend renders variant-appropriate narratives by dispatching on `headline_claim.kind`; no paper-id substring matching anywhere downstream of A1. D2 is skipped automatically when the variant isn't directly comparable to `BacktestResult.mean_return` (Sharpe-Δ, VR statistic, statistical test) — see `_headline_claim_comparable_to_mean_return` in `app/main.py`.

`extract_and_verify` (A1+A2 orchestration) keeps the self-consistency check on `spec.headline_claim` for the return-bearing variants — a non-zero t-stat with `monthly_return=0.0` is mathematically impossible and gets force-nulled. A new **`PaperExtractionOverride`** mechanism (`src/specs/extraction_overrides.py`, loaded from `data/extraction_overrides/*.yaml`) covers the PDF-unrecoverable cases (Lo-MacKinlay 1988 byte-scrambled, AMP-2013 mirrored Table I): each override is bound to a SHA-256 hash of a verbatim PDF span, so replacing the PDF silently disables the override rather than carrying a stale curated value forward.

**`PortfolioSpec` enforces a canonical long-short encoding (Phase D):** for `long_short=True` specs, `long_bucket = n_buckets` and `short_bucket = 1`; the sign of the strategy is carried entirely by `signal.direction` (`compute_signal` negates the score when `direction='long_low'` so bucket N is always the paper's chosen long leg). Validator rejects any other combination — the historical CHST 2017 case where A1 emitted `(direction='long_low', long_bucket=1, short_bucket=5)` and the engine cancelled the two flips to trade momentum is now structurally unrepresentable.

Two narrow paper-id overrides remain, documented as exceptions: `extraction_verifier.PAPER_ID_OVERRIDES["jegadeesh_titman_1993"]["universe.min_price"] = 10.0` (paper is silent on a price filter; $10 matches the published replication community default) and `app/main.py::ENGINE_WINDOW_OVERRIDES["jegadeesh_titman_1993"] = (2007-01-01, 2026-04-02)` (paper window predates the data panel by ~30 years; OOS window captures the post-2009 momentum revival). Both are config-shaped knobs with stated reasons. For new papers, prefer A1 prompt-level conventions or a quote-checksum-bound `PaperExtractionOverride`.

See `DESIGN_NOTES.md` for non-obvious design decisions and bug postmortems, and `CLAUDE.md` for agent instructions on how to extend this repo.

## Roadmap (Phase 5+)

Phases 3 and 4 already shipped: the D2 divergence diagnostician (LLM-proposed spec mutations validated by engine reruns) is live in `src/agents/validation/divergence_diagnostician.py`, and the six-family robustness battery (`lag`, `costs`, `subperiod`, `liquidity`, `data_quality`, `capacity`) plus the D3 robustness adversary are live in `src/robustness/` and `src/agents/validation/robustness_adversary.py`. Both are exposed via `/api/diagnose` and `/api/robustness` and rendered in the Diagnosis and Robustness tabs. Each battery family is wrapped in try/except so a single family's failure (e.g. capacity hitting a value-weighting edge case on AMP-2013) is recorded as a `fragility_signal` rather than killing the whole battery.

Still planned, on top of the Phase 1–4 foundation:

- **Report Synthesizer (E1)** — single self-contained HTML artifact stitching spec + verification + critique + backtest + diagnosis + robustness with click-through provenance back to the underlying `SupportingQuote`s and `ProvenanceRecord`s. This is overview.md Step 13 and the natural next deliverable now that every upstream agent emits a typed spec.
- **Tradeability Scorecard** — compute turnover, capacity, rebalance frequency, and gross/net spread from `BacktestResult`, then run an LLM heuristic (Sonnet) that emits a CIO-style verdict — `tradeable` / `borderline` / `not tradeable` — with reasons tied to the underlying metrics. Distinct from D3's implementability verdict in that it focuses on portfolio-construction realism rather than fragility-under-stress.
- **Paper-vs-replication diff** — A1 extracts `HeadlineClaim` (`src/specs/paper_metrics.py`) with the paper's self-reported `monthly_return` + `t_stat` + `window_label` plus a verbatim `SupportingQuote` that A2 verifies at high severity. D2 picks this up automatically as its comparison target. The A1 prompt requires `headline_claim` for empirical papers (only purely theoretical papers may set it to `null`); when the paper reports a sweep with no designated primary, A1 must populate the field with an inferred value AND raise a high-severity `AmbiguityFlag` with `parameter="headline_claim"`. The remaining UI piece is a side-by-side "paper says X, we got Y, delta = Δ" table on the Backtest tab — the data is already on the bundle as `verified_spec.spec.headline_claim` and `paper_claim` (the projected flat shape that drives D2).
- **News contextualization (guarded)** — optional tab that pulls headlines around the largest drawdown windows. Needs an A2-style verifier (every headline must cite a retrievable URL + publication date inside the drawdown window) because a raw LLM will happily invent plausible stories. Gated behind an env flag; off by default.
