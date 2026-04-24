# CLAUDE.md

Agent instructions for Claude Code working on the Paper Replication Machine.

## Golden rules

1. **Keep the README current.** Whenever you change models, endpoints, CLI entry points, directory structure, data dependencies, environment variables, or setup steps — update `README.md` in the same change. Don't ship a commit that makes README wrong.
2. **Don't modify `src/` for frontend reasons.** `src/` is the Phase 1+2 core (specs, engine, data store, agents). The `app/` MVP consumes it unchanged. If a frontend tweak seems to need a spec or engine change, push back and ask — it usually means the schema is being misread, not that the schema is wrong.
3. **Use the venv Python explicitly**: `.venv/bin/python` and `.venv/bin/pip`. A shell alias otherwise routes `python` to an unrelated interpreter.
4. **Specs are the contract.** Every cross-component payload is a Pydantic v2 model in `src/specs/`. If a value isn't represented there, it doesn't exist as far as the pipeline is concerned — don't smuggle data through dicts or free-form strings.
5. **Every supporting quote must be verbatim.** No lowercasing, no unicode folding, no whitespace collapsing. The quote verifier owns all normalization; stored quotes stay exactly as the PDF spells them.
6. **Respect the cache.** `src/utils/llm.py` caches on (model, system_prompt, user_content, schema name + schema JSON). Changing a prompt or a spec field invalidates cleanly — don't write manual cache busts.

## Project map

```
src/specs/          Pydantic contracts (ReplicationSpec, BacktestResult, VerifiedReplicationSpec, …)
src/data/           PointInTimeDataStore + DefeatBetaYahooSource — every call needs as_of_date
src/engine/         Canonical backtest — one tested implementation
src/pdf/            pdfplumber parser + deterministic fuzzy quote verifier
src/agents/
  extraction/       A1 methodology_extractor, A2 extraction_verifier, A3 adversarial_reviewer
  implementation/   B1 data_mapper scaffold (not yet orchestrated)
  prompts/          .md system prompts (diff-reviewable)
src/utils/llm.py    Anthropic wrapper — structured tool-use, schema retry, disk cache
app/                MVP FastAPI + static SPA (Phase 1/2 UI)
scripts/            phase1_gate.py, run_jt_end_to_end.py, run_a1_on_jt.py
tests/              85+ pytest tests. Mark LLM-calling tests with @pytest.mark.llm.
```

## Models in use

| Agent | Model | Why |
|-------|-------|-----|
| A1 methodology_extractor | `claude-sonnet-4-6` | Fits inside 30k-TPM on long papers; quality is close to Opus for well-structured methodology sections. |
| A2 extraction_verifier (support check) | `claude-haiku-4-5-20251001` | Cheap per-quote yes/no/partial judgments. |
| A3 adversarial_reviewer | `claude-sonnet-4-6` | Three-criticism format needs reasoning, not quota headroom. |
| B1 data_mapper | `claude-sonnet-4-6` | Scaffold only; not wired yet. |

Previous iterations used Opus 4.7 on A1/A3/B1. Swap back to `claude-opus-4-7` if you have higher-tier API access and want the extra quality — just change `MODEL = "..."` at the top of each agent module and note the change in `README.md`.

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

## MVP frontend (`app/static/`)

- Single-page SPA, no build step, no Tailwind CDN — hand-rolled CSS matching the "Dark Mode Research Lab" tokens.
- Six tabs: Overview, Spec, Verification, Critique, Backtest, Provenance.
- `safeJson()` in `app.js` reads responses as text first so non-JSON bodies surface legibly instead of `Unexpected token ...`.
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

## Update policy for this file

When you change anything that affects how someone runs, extends, or reasons about this repo, update both:
- `README.md` — user-facing surface (setup, architecture, endpoints)
- `CLAUDE.md` — agent-facing rules (models, gotchas, task discipline)

If a change doesn't touch either document's content, you don't need to edit them — but confirm to the user that you checked.
