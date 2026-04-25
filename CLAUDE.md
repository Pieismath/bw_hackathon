# CLAUDE.md

Agent instructions for Claude Code working on the Paper Replication Machine.

## Golden rules

1. **Keep README.md AND CLAUDE.md current on every change.** This is a standing instruction: every prompt that modifies code, adds endpoints / scripts / agents, or changes models / environment / directory structure / data dependencies / setup steps MUST update both `README.md` and `CLAUDE.md` in the same change. Don't ship a commit that makes either document wrong. If neither document needs an edit for a given change, explicitly confirm to the user that you checked both and concluded no edit was warranted — silence is not acceptable.
2. **Don't modify `src/` for frontend reasons.** `src/` is the Phase 1+2 core (specs, engine, data store, agents). The `app/` MVP consumes it unchanged. If a frontend tweak seems to need a spec or engine change, push back and ask — it usually means the schema is being misread, not that the schema is wrong.
3. **Use the venv Python explicitly**: `.venv/bin/python` and `.venv/bin/pip`. A shell alias otherwise routes `python` to an unrelated interpreter.
4. **Specs are the contract.** Every cross-component payload is a Pydantic v2 model in `src/specs/`. If a value isn't represented there, it doesn't exist as far as the pipeline is concerned — don't smuggle data through dicts or free-form strings.
5. **Every supporting quote must be verbatim.** No lowercasing, no unicode folding, no whitespace collapsing. The quote verifier owns all normalization; stored quotes stay exactly as the PDF spells them.
6. **Respect the cache.** `src/utils/llm.py` caches on (model, system_prompt, user_content, schema name + schema JSON). Changing a prompt or a spec field invalidates cleanly — don't write manual cache busts.

## Project map

```
src/specs/          Pydantic contracts (ReplicationSpec, BacktestResult, VerifiedReplicationSpec,
                    DivergenceDiagnosis, RobustnessScorecard, RobustnessJudgment, …)
src/data/           PointInTimeDataStore + DefeatBetaYahooSource — every call needs as_of_date
src/engine/         Canonical backtest — one tested implementation
src/pdf/            pdfplumber parser + deterministic fuzzy quote verifier
src/robustness/     Phase 4 stress-test battery (lag, costs, subperiod, liquidity, data_quality, capacity)
src/agents/
  extraction/       A1 methodology_extractor, A2 extraction_verifier, A3 adversarial_reviewer
  implementation/   B1 data_mapper + B2 mapping_verifier
  validation/       D1 result_comparator (deterministic), D2 divergence_diagnostician,
                    D3 robustness_adversary
  prompts/          .md system prompts (diff-reviewable)
src/utils/llm.py    Anthropic wrapper — structured tool-use, schema retry, disk cache
app/                MVP FastAPI + static SPA (Phase 1–4 UI)
scripts/            phase1_gate.py, run_jt_end_to_end.py, run_a1_on_jt.py,
                    run_d2_on_jt.py, run_phase4_on_jt.py, run_phase4_postfix.py,
                    run_jt_full_pipeline.py, run_oos_dbt_1985.py, mentor_demo.py
tests/              100+ pytest tests across 16 files. Mark LLM-calling tests with @pytest.mark.llm.
```

## Models in use

| Agent | Model | Why |
|-------|-------|-----|
| A1 methodology_extractor | `claude-sonnet-4-6` | Fits inside 30k-TPM on long papers; quality is close to Opus for well-structured methodology sections. |
| A2 extraction_verifier (support check) | `claude-haiku-4-5-20251001` | Cheap per-quote yes/no/partial judgments. |
| A3 adversarial_reviewer | `claude-sonnet-4-6` | Three-criticism format needs reasoning, not quota headroom. |
| B1 data_mapper | `claude-sonnet-4-6` | Maps spec fields onto concrete data sources. |
| B2 mapping_verifier | `claude-haiku-4-5-20251001` | Cheap yes/no judgments on mapping correctness. |
| D1 result_comparator | _(deterministic — no LLM)_ | Pure numerical comparison of paper claims vs `BacktestResult`. |
| D2 divergence_diagnostician | `claude-sonnet-4-6` | Mutation proposals + residual-cause narrative — needs reasoning, not tight quota headroom. |
| D3 robustness_adversary | `claude-sonnet-4-6` | Interprets the robustness scorecard into a single CIO verdict; uses the same tier as A3 for structured judgment. |

Previous iterations used Opus 4.7 on A1/A3/B1/D2/D3. Swap back to `claude-opus-4-7` if you have higher-tier API access and want the extra quality — just change `MODEL = "..."` at the top of each agent module and note the change in `README.md` AND `CLAUDE.md`.

## How to run things

```bash
# Tests
.venv/bin/python -m pytest tests/ -q                         # all
.venv/bin/python -m pytest tests/ -q -m "not llm"            # skip live API tests
.venv/bin/python -m pytest tests/test_backtest_engine.py -q  # engine only

# Engine validation
.venv/bin/python -u scripts/phase1_gate.py

# Full Phase 2 demo on Jegadeesh-Titman 1993
.venv/bin/python -u scripts/run_jt_end_to_end.py

# Full A1→A2→A3→engine→D1→D2→battery→D3 pipeline on JT 1993
.venv/bin/python -u scripts/run_jt_full_pipeline.py

# Phase 4 only — robustness battery + D3 judgment on JT
.venv/bin/python -u scripts/run_phase4_on_jt.py

# Phase 3 only — D2 divergence diagnostician on JT gap
.venv/bin/python -u scripts/run_d2_on_jt.py

# Mentor-review narrative demo (top-to-bottom story)
.venv/bin/python -u scripts/mentor_demo.py

# MVP web server
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# → http://127.0.0.1:8000 (LOAD DEMO renders without any API key)

# Rebuild the demo bundle JSON
.venv/bin/python -m app.demo_fixture   # writes outputs/demo_bundle.json
```

## Environment

`.env` at repo root, loaded with `override=True` because the macOS Claude desktop bundle ships an empty `ANTHROPIC_API_KEY` that would otherwise win:

```
ANTHROPIC_API_KEY=sk-ant-…   # required for A1/A2/A3 live runs
FRED_API_KEY=…               # optional — ALFRED macro data
HF_TOKEN=…                   # optional — HuggingFace dataset downloads
```

The `/api/demo` endpoint works with none of these set.

## MVP backend (`app/main.py`)

All endpoints return JSON. Handlers catch `BaseException`, log the traceback, and return `{"error": "...", "traceback_tail": "...", "hint": "..."}` with the appropriate status code — nothing should escape as starlette's HTML "Internal Server Error".

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Serves `app/static/index.html` |
| `/static/*` | GET | Static assets |
| `/api/demo` | GET | Full schema-valid JT-1993 bundle (no external deps) |
| `/api/papers` | GET | List uploaded PDFs in `data/papers/` |
| `/api/papers` | POST | Multipart PDF upload (paper_id = filename stem) |
| `/api/extract` | POST | Run A1 + A2 via `extract_and_verify` |
| `/api/critique` | POST | Run A3 via `adversarial_reviewer.review` |
| `/api/backtest` | POST | Run `run_backtest` — 503 if parquet cache missing |
| `/api/robustness` | POST | Run `src.robustness.run_battery` + D3 `judge` on a spec — 503 if parquet cache missing, same pattern as `/api/backtest` |
| `/api/diagnose` | POST | Run D2 `diagnose` on a spec + paper-claim pair. Requires `ANTHROPIC_API_KEY` |

## MVP frontend (`app/static/`)

- Single-page SPA, no build step, no Tailwind CDN — hand-rolled CSS matching the "Dark Mode Research Lab" tokens.
- Eight tabs: Overview, Spec, Verification, Critique, Backtest, Robustness, Diagnosis, Provenance. (Eight stays eight — the Run config dial panel is a card on the Overview tab, not a new tab.)
- `safeJson()` in `app.js` reads responses as text first so non-JSON bodies surface legibly instead of `Unexpected token ...`.
- The Run config panel on the Overview tab exposes 13 dials that override `ReplicationSpec` fields (plus `transaction_cost_bps` as a request-level kwarg) before each pipeline call. `applyDials(spec)` in `app.js` is the single contract between dial-form state and the outgoing `spec` dict — it deep-clones the original A1 spec and merges the form values without touching `supporting_quote`s. Any new dial must extend `applyDials` (and its inverse `seedDialFormFromSpec`); don't smuggle form state into individual `fetch` call sites.
- Charts are hand-built SVG (no chart lib): `EquityChart` (Backtest tab) supports drag-to-zoom + dbl-click reset + hover tooltip + drawdown subpanel + `REGIMES` shading; `buildDecayCard` (Robustness tab) renders a small lag/cost decay curve with a marker for `lag_half_life_days` / `cost_threshold_bps`; `buildGapWaterfall` (Diagnosis tab) renders one bar per `MutationResult` from `pre_abs_gap` to `post_abs_gap`, green when the gap closed and red when it widened. If a chart needs a new field, render it on the existing primitive — don't add a new chart library.
- If you add a new API field, render it in the corresponding tab — don't leave the UI silently ignoring backend additions.

## Things that have bitten us (short version — full postmortems in `DESIGN_NOTES.md`)

- **`pandas.DateOffset(months=N)` is not time-reversible.** For month-end bookkeeping, use inclusive ranges `[lower, upper)` and positional offsets on a pre-sorted calendar, never raw date arithmetic. One character (`>` → `>=`) at `src/engine/tranches.py::active_tranches_in` accounted for 7/12 missing months per year.
- **Yahoo-sourced universe excludes delisted names.** Always propagate survivorship bias via `ProvenanceRecord.fidelity_note` and `BacktestResult.data_quality_flags`. The UI renders these as a yellow banner — if you add a new data source, do the same.
- **Quote normalization lives in the verifier, not in the model.** Stored `SupportingQuote.text` stays verbatim; `src/pdf/quote_verifier.py` normalizes both sides before comparing.

## Task discipline

- Don't commit unless asked.
- Create new commits rather than amending — pre-commit hook failures mean the commit didn't happen.
- Never use `--no-verify`, `--force`, or `--hard` unless the user explicitly asks.
- Never edit `.env` or anything that looks like secrets.

## Planned Phase 5+ work

Phase 3 (D2 divergence diagnostician) and Phase 4 (six-family robustness battery + D3 robustness adversary) already shipped — see `src/agents/validation/` and `src/robustness/`. The remaining roadmap items below are net-new. When you pick one up, follow the same spec-first discipline as Phase 1–4: new cross-component payloads go in `src/specs/`, new agents get a `.md` prompt under `src/agents/prompts/`, and the MVP UI gets a matching tab (don't silently ignore new backend fields).

- **Report Synthesizer (E1)** — single self-contained HTML artifact stitching spec + verification + critique + backtest + diagnosis + robustness with click-through provenance back to the underlying `SupportingQuote`s and `ProvenanceRecord`s. This is overview.md Step 13. Should be a pure rendering layer over already-typed specs — no new LLM call, no schema bloat.
- **Tradeability Scorecard** — derive turnover / capacity / rebalance frequency / gross-net spread from `BacktestResult`, then an LLM heuristic (Sonnet) emits `{verdict: tradeable|borderline|not_tradeable, reasons: [...]}`. Spec lives in `src/specs/tradeability.py`; agent in `src/agents/analysis/tradeability_scorer.py`. Distinct from D3's implementability verdict (which is fragility-under-stress) — this one is portfolio-construction realism. New tab on the frontend.
- **Paper-vs-replication diff** — extend A1 to extract `PaperReportedMetrics { sharpe, mean_return, t_stat, ... }` with verbatim `SupportingQuote`s (A2 verifies them same as any other quote). Render a side-by-side diff table on the Backtest tab. This needs a prompt change + spec addition; keep the A1 prompt change small and add a new schema rather than bloating `ReplicationSpec`.
- **News contextualization (guarded)** — optional tab that pulls headlines around drawdown windows. MUST include an A2-style verifier: every headline requires a retrievable URL and a publication date inside the drawdown window, or it's dropped. Gate behind an env flag (e.g. `ENABLE_NEWS_CONTEXT=1`); default off. This is the highest hallucination-risk feature in the roadmap — do not ship without the verifier.

## Update policy for this file

Every change to this repo that affects models, endpoints, scripts, specs, or architecture MUST edit both `README.md` and `CLAUDE.md` in the same commit. No exceptions:

- `README.md` — user-facing surface (setup, architecture, endpoints, roadmap)
- `CLAUDE.md` — agent-facing rules (models, gotchas, task discipline, project map)

"Confirm you checked" is only acceptable when no content genuinely needs to change — and you must say so out loud in the response (e.g. "I checked README.md and CLAUDE.md; neither needs an edit because this change is purely internal to `src/engine/metrics.py` and doesn't alter any documented surface."). Silence is not acceptable.
